"""Asynchronous ELM327 driver.

Responsibilities:
* serialise commands (one outstanding command per adapter, ``asyncio.Lock``)
* wait for the ``>`` prompt with a timeout and resynchronise after timeouts
* initialise the adapter (echo/linefeeds/spaces off, headers on, auto protocol)
* enforce the read-only policy: only whitelisted OBD services and AT commands
  can ever be transmitted to the vehicle
* translate adapter error strings into exceptions
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field

from .exceptions import (
    AdapterError,
    AdapterTimeout,
    ForbiddenCommand,
    NoDataError,
    ResponseParseError,
    TransportError,
    VehicleUnreachable,
)
from .protocol import ELM_PROTOCOLS, BusKind, Message, assemble_messages
from .transport.base import Transport

_LOGGER = logging.getLogger(__name__)

PROMPT = b">"

#: Diagnostic services that are allowed to be sent. All of them only READ data.
#: 0x01 current data, 0x02 freeze frame, 0x03 stored DTCs, 0x07 pending DTCs,
#: 0x09 vehicle information, 0x0A permanent DTCs,
#: 0x21 KWP2000 ReadDataByLocalIdentifier, 0x22 UDS ReadDataByIdentifier.
#: Explicitly NOT allowed: 0x04 (clear DTCs), 0x08 (on-board control),
#: 0x10 (session control), 0x11 (reset), 0x14 (clear DTC), 0x27 (security),
#: 0x2E (write DID), 0x2F (IO control), 0x31 (routine), 0x34+ (download), ...
ALLOWED_SERVICES: frozenset[int] = frozenset({0x01, 0x02, 0x03, 0x07, 0x09, 0x0A, 0x21, 0x22})

#: AT commands that may be used. Everything here only changes volatile adapter
#: state (lost on power cycle) – notably the ``PP`` (programmable parameters,
#: stored in EEPROM), ``MA``/``MR``/``MT`` (bus monitoring), ``BD``, ``WS``
#: etc. are not allowed.
_ALLOWED_AT = re.compile(
    r"^AT("
    r"Z|WS|D|E[01]|L[01]|S[01]|H[01]|M0|AT[012]|ST[0-9A-F]{2}|SP[0-9A-C]|SPA[0-9A-C]|"
    r"DPN?|I|@1|RV|CAF[01]|CFC[01]|AR|CRA([0-9A-FX]{3}|[0-9A-FX]{8})?|"
    r"SH([0-9A-F]{3}|[0-9A-F]{6})|CP[0-9A-F]{2}|"
    r"FCSH([0-9A-F]{3}|[0-9A-F]{8})|FCSD([0-9A-F]{2}){1,5}|FCSM[012]|"
    r"CEA([0-9A-F]{2})?|R[01]|AL|NL|DESC"
    r")$"
)

_IGNORED_LINES = ("SEARCHING", "BUS INIT", "OK")

_NO_DATA = ("NO DATA",)
_UNREACHABLE = (
    "UNABLE TO CONNECT",
    "CAN ERROR",
    "BUS ERROR",
    "BUS BUSY",
    "FB ERROR",
    "ERR",  # ERRxx (e.g. ERR94 fatal CAN error)
    "LV RESET",
    "ACT ALERT",
    "STOPPED",
)
_ADAPTER_ERRORS = ("?", "BUFFER FULL", "DATA ERROR", "<DATA ERROR", "<RX ERROR")


#: AT commands whose argument is cached (key = command prefix)
_AT_KEYS = (
    "FCSH", "FCSD", "FCSM", "CRA", "CAF", "CFC", "CEA", "SH", "CP", "ST", "SP", "AT", "AR", "H", "S",
)

#: Requests whose answers contain identifying data (VIN) – never logged.
_PRIVATE_REQUESTS = ("0902",)


def _at_key(cmd: str) -> str:
    body = cmd[2:]
    for key in _AT_KEYS:
        if body.startswith(key):
            return "CRA" if key == "AR" else key
    return body


def _normalise(command: str) -> str:
    return command.replace(" ", "").upper()


def validate_command(command: str) -> str:
    """Return the normalised command or raise ForbiddenCommand."""
    cmd = _normalise(command)
    if cmd.startswith("AT"):
        if not _ALLOWED_AT.match(cmd):
            raise ForbiddenCommand(f"AT command not allowed: {command}")
        return cmd
    if not re.fullmatch(r"([0-9A-F]{2})+", cmd):
        raise ForbiddenCommand(f"Malformed OBD request: {command}")
    service = int(cmd[:2], 16)
    if service not in ALLOWED_SERVICES:
        raise ForbiddenCommand(
            f"Service 0x{service:02X} is not a read-only service and is blocked"
        )
    return cmd


@dataclass(slots=True)
class AdapterInfo:
    """Information about the adapter, gathered during initialisation."""

    identity: str | None = None  # ATI, e.g. "ELM327 v2.3"
    description: str | None = None  # AT@1
    protocol_number: str | None = None
    protocol_name: str | None = None
    bus: BusKind | None = None


@dataclass(slots=True)
class CommandStats:
    """Statistics for diagnostics."""

    sent: int = 0
    timeouts: int = 0
    errors: int = 0
    no_data: int = 0
    last_commands: list[str] = field(default_factory=list)

    def remember(self, entry: str) -> None:
        self.last_commands.append(entry)
        del self.last_commands[:-30]


class ELM327:
    """ELM327 protocol driver on top of a :class:`Transport`."""

    def __init__(
        self,
        transport: Transport,
        *,
        command_timeout: float = 5.0,
        log_traffic: bool = False,
    ) -> None:
        self._transport = transport
        self._timeout = command_timeout
        self._log_traffic = log_traffic
        self._lock = asyncio.Lock()
        self._buffer = bytearray()
        self._prompt = asyncio.Event()
        self._needs_resync = False
        self._at_state: dict[str, str] = {}
        self._base_protocol: str | None = None
        self.info = AdapterInfo()
        self.stats = CommandStats()
        transport.set_data_callback(self._on_data)

    # ------------------------------------------------------------------ I/O
    def _on_data(self, data: bytes) -> None:
        self._buffer.extend(data)
        if PROMPT in data:
            self._prompt.set()

    @property
    def transport(self) -> Transport:
        return self._transport

    @property
    def bus(self) -> BusKind | None:
        return self.info.bus

    async def _raw(self, command: str, timeout: float) -> list[str]:
        """Send ``command`` and return the response lines (lock must be held)."""
        if not self._transport.is_connected:
            raise TransportError("Adapter link is down")
        if self._needs_resync:
            await self._resync()
        self._buffer.clear()
        self._prompt.clear()
        started = time.monotonic()
        self.stats.sent += 1
        await self._transport.write(command.encode("ascii") + b"\r")
        try:
            await asyncio.wait_for(self._prompt.wait(), timeout)
        except TimeoutError as err:
            self.stats.timeouts += 1
            self._needs_resync = True
            self.stats.remember(f"{command} -> TIMEOUT")
            raise AdapterTimeout(f"No prompt after {command!r} within {timeout}s") from err
        raw = bytes(self._buffer)
        text = raw.split(PROMPT, 1)[0].decode("ascii", errors="replace")
        text = text.replace("\x00", "")
        lines = [ln.strip() for ln in re.split(r"[\r\n]+", text) if ln.strip()]
        if lines and _normalise(lines[0]) == _normalise(command):
            lines = lines[1:]  # echo still on (before ATE0 took effect)
        elapsed = (time.monotonic() - started) * 1000
        summary = " | ".join(lines)
        if command.startswith(_PRIVATE_REQUESTS):
            summary = f"<{len(lines)} lines redacted>"
        self.stats.remember(f"{command} -> {summary} ({elapsed:.0f} ms)")
        if self._log_traffic:
            _LOGGER.debug("ELM >> %s  << %s  (%.0f ms)", command, summary, elapsed)
        return lines

    async def _resync(self) -> None:
        """Bring the adapter back to a known state after a timeout.

        Any character interrupts a running ELM327 search; we send a bare CR and
        wait for the prompt, discarding everything.
        """
        self._needs_resync = False
        self._buffer.clear()
        self._prompt.clear()
        await self._transport.write(b"\r")
        try:
            await asyncio.wait_for(self._prompt.wait(), 2.0)
        except TimeoutError:
            _LOGGER.debug("Adapter did not answer resync")
        await asyncio.sleep(0.05)
        self._buffer.clear()
        self._prompt.clear()

    # ------------------------------------------------------------- commands
    async def at(self, command: str, *, timeout: float | None = None) -> list[str]:
        """Send an AT command and return its response lines."""
        cmd = validate_command(command)
        async with self._lock:
            lines = await self._raw(cmd, timeout or self._timeout)
        if lines and lines[-1] == "?":
            self.stats.errors += 1
            raise AdapterError(f"Adapter rejected {cmd}")
        return lines

    async def at_cached(self, key: str, command: str) -> None:
        """Send an AT command only when the cached state differs."""
        cmd = _normalise(command)
        if self._at_state.get(key) == cmd:
            return
        await self.at(cmd)
        self._at_state[key] = cmd

    async def initialize(self) -> AdapterInfo:
        """Reset and configure the adapter. Does not talk to the vehicle."""
        self._at_state.clear()
        async with self._lock:
            # A reset may answer slowly and may echo; ignore the content.
            try:
                await self._raw("ATZ", 5.0)
            except AdapterTimeout:
                # Some clones never send a prompt after ATZ; try warm start.
                await self._raw("ATWS", 5.0)
            await asyncio.sleep(0.3)
        await self.at("ATE0")
        await self.at("ATL0")
        await self.at("ATS0")
        await self.at("ATH1")
        await self.at("ATAT1")  # adaptive timing
        identity = await self.at("ATI")
        self.info.identity = identity[-1] if identity else None
        try:
            desc = await self.at("AT@1")
            self.info.description = desc[-1] if desc else None
        except AdapterError:
            self.info.description = None
        await self.at("ATSP0")  # automatic protocol detection
        return self.info

    async def read_adapter_voltage(self) -> float | None:
        """Voltage at OBD pin 16 (12 V battery) as measured by the adapter."""
        lines = await self.at("ATRV")
        for line in lines:
            match = re.search(r"(\d+(?:\.\d+)?)\s*V?", line)
            if match:
                return float(match.group(1))
        return None

    async def detect_protocol(self) -> AdapterInfo:
        """Trigger the automatic protocol search and read the result.

        Raises :class:`VehicleUnreachable` when no ECU answers.
        """
        lines = await self._query_raw("0100", timeout=max(self._timeout, 15.0))
        self._raise_for_errors("0100", lines)
        number = (await self.at("ATDPN"))[-1].upper().lstrip("A")
        name, bus = ELM_PROTOCOLS.get(number, (f"Unknown ({number})", None))
        if bus is None:
            raise VehicleUnreachable(f"Unsupported protocol {number}")
        # Lock the detected protocol, so a later sleeping bus does not trigger
        # a slow new search on every request.
        await self.at(f"ATSP{number}")
        self._at_state["SP"] = f"ATSP{number}"
        self._base_protocol = number
        self.info.protocol_number = number
        self.info.protocol_name = name
        self.info.bus = bus
        _LOGGER.debug("Vehicle protocol detected: %s (%s)", name, number)
        return self.info

    async def _query_raw(self, request: str, *, timeout: float | None = None) -> list[str]:
        cmd = validate_command(request)
        async with self._lock:
            return await self._raw(cmd, timeout or self._timeout)

    def _raise_for_errors(self, request: str, lines: list[str]) -> list[str]:
        """Filter informational lines and raise for error answers."""
        data_lines: list[str] = []
        for line in lines:
            upper = line.upper()
            if upper.startswith(_IGNORED_LINES):
                continue
            if any(upper.startswith(e) for e in _NO_DATA):
                continue
            if any(upper.startswith(e) for e in _UNREACHABLE):
                self.stats.errors += 1
                raise VehicleUnreachable(f"{request}: {line}")
            if upper in _ADAPTER_ERRORS or upper.startswith("<"):
                self.stats.errors += 1
                raise AdapterError(f"{request}: {line}")
            data_lines.append(line)
        if not data_lines:
            self.stats.no_data += 1
            raise NoDataError(f"{request}: NO DATA")
        return data_lines

    async def set_header(self, header: str | None) -> None:
        """Set the transmit header; ``None`` restores the OBD functional header."""
        bus = self.info.bus
        if header is None:
            header = "18DB33F1" if bus is BusKind.CAN_29 else "7DF"
            if bus is BusKind.LEGACY:
                header = "686AF1"
        header = header.upper()
        if len(header) == 8:  # 29 bit: priority byte + 3 header bytes
            await self.at_cached("CP", f"ATCP{header[:2]}")
            await self.at_cached("SH", f"ATSH{header[2:]}")
        else:
            await self.at_cached("SH", f"ATSH{header}")

    async def set_receive_filter(self, address: str | None) -> None:
        """Only accept responses from ``address`` (``None`` = automatic)."""
        if address is None:
            await self.at_cached("CRA", "ATAR")
        else:
            await self.at_cached("CRA", f"ATCRA{address}")

    async def run_init_commands(self, commands: Iterable[str]) -> None:
        """Run (whitelisted) AT commands required by a vehicle profile."""
        for command in commands:
            cmd = validate_command(command)
            if not cmd.startswith("AT"):
                raise ForbiddenCommand(f"Init sequences may only contain AT commands: {command}")
            key = _at_key(cmd)
            await self.at_cached(key, cmd)
            if key == "SP":
                number = cmd[4:].lstrip("A")
                if number in ELM_PROTOCOLS:
                    self.info.bus = ELM_PROTOCOLS[number][1]

    async def reset_addressing(self) -> None:
        """Restore functional addressing after manufacturer specific requests."""
        await self.set_receive_filter(None)
        if self._at_state.get("FCSM") not in (None, "ATFCSM0"):
            await self.at_cached("FCSM", "ATFCSM0")
        if self._base_protocol and self._at_state.get("SP") != f"ATSP{self._base_protocol}":
            await self.at_cached("SP", f"ATSP{self._base_protocol}")
            self.info.bus = ELM_PROTOCOLS[self._base_protocol][1]
        if self.info.bus is BusKind.CAN_29 or self._at_state.get("CP") not in (None, "ATCP18"):
            await self.at_cached("CP", "ATCP18")
        await self.set_header(None)

    async def query(
        self,
        request: str,
        *,
        timeout: float | None = None,
        expected_service: int | None = None,
    ) -> list[Message]:
        """Send an OBD/UDS request and return the decoded messages.

        Raises NoDataError, VehicleUnreachable, AdapterError, AdapterTimeout,
        ResponseParseError.
        """
        if self.info.bus is None:
            raise VehicleUnreachable("Protocol not detected yet")
        lines = await self._query_raw(request, timeout=timeout)
        data_lines = self._raise_for_errors(request, lines)
        messages = assemble_messages(data_lines, self.info.bus)
        service = expected_service if expected_service is not None else int(request[:2], 16)
        positive = [m for m in messages if m.data and m.data[0] == service + 0x40]
        if positive:
            return positive
        negatives = [m for m in messages if m.is_negative]
        if negatives:
            nrc = negatives[0].nrc
            raise NoDataError(f"{request}: negative response NRC 0x{nrc:02X}")
        raise ResponseParseError(f"{request}: unexpected response {data_lines!r}")

    def diagnostics(self) -> dict[str, object]:
        return {
            "identity": self.info.identity,
            "description": self.info.description,
            "protocol": self.info.protocol_name,
            "protocol_number": self.info.protocol_number,
            "commands_sent": self.stats.sent,
            "timeouts": self.stats.timeouts,
            "errors": self.stats.errors,
            "no_data": self.stats.no_data,
            "recent_traffic": list(self.stats.last_commands),
        }
