"""ELM327 + vehicle simulator.

Used by the automated tests and by the "Simulator" connection type in the
config flow, so the whole integration can be exercised without a car. The
simulator produces the same byte stream a real ELM327 produces (echo,
headers, spaces, ISO-TP multi-frame, ``>`` prompt, BLE-sized notification
chunks) and supports fault injection.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from .exceptions import TransportError, TransportUnavailable
from .transport.base import Transport


def wican_payload(length: int, values: dict[int, int], prefix: bytes) -> bytes:
    """Build a payload so that WiCAN byte index ``i`` holds ``values[i]``.

    ``prefix`` (e.g. ``62 01 01``) is placed at the start of the payload.
    """
    payload = bytearray(length)
    payload[: len(prefix)] = prefix
    multi = length > 7
    for index, value in values.items():
        if not multi:
            offset = index - 1
        elif index < 8:
            offset = index - 2
        else:
            frame, pos = divmod(index, 8)
            if pos == 0:
                raise ValueError(f"WiCAN index {index} is a PCI byte")
            offset = 6 + (frame - 1) * 7 + (pos - 1)
        payload[offset] = value & 0xFF
    return bytes(payload)


@dataclass
class SimulatedVehicle:
    """State of the simulated vehicle."""

    vin: str | None = "WVWZZZ1KZAW000001"
    protocol: str = "6"  # 11 bit 500k
    ignition_on: bool = True
    #: ecu id -> {pid: data bytes}
    mode01: dict[str, dict[int, bytes]] = field(default_factory=dict)
    stored_dtcs: list[str] = field(default_factory=list)
    pending_dtcs: list[str] = field(default_factory=list)
    permanent_dtcs: list[str] = field(default_factory=list)
    #: (transmit header, request hex) -> (responder id, payload incl. response SID)
    extended: dict[tuple[str, str], tuple[str, bytes]] = field(default_factory=dict)
    multi_pid: bool = True
    battery_voltage: float = 12.6


def _dtc_bytes(code: str) -> bytes:
    letter = "PCBU".index(code[0])
    high = (letter << 6) | (int(code[1]) << 4) | int(code[2], 16)
    return bytes([high, int(code[3:5], 16)])


def demo_combustion_vehicle() -> SimulatedVehicle:
    """A petrol car with typical engine values."""
    return SimulatedVehicle(
        vin="WVWZZZAUZHW000042",
        mode01={
            "7E8": {
                0x01: bytes([0x81, 0x07, 0x65, 0x00]),  # MIL on, 1 DTC
                0x04: bytes([0x33]),
                0x05: bytes([0x7B]),  # 83 °C
                0x0B: bytes([0x21]),
                0x0C: bytes([0x0B, 0xB8]),  # 750 rpm
                0x0D: bytes([0x00]),
                0x0F: bytes([0x3C]),  # 20 °C
                0x10: bytes([0x01, 0x90]),
                0x11: bytes([0x26]),
                0x1F: bytes([0x00, 0x3C]),
                0x21: bytes([0x00, 0x00]),
                0x2F: bytes([0x80]),  # 50.2 %
                0x31: bytes([0x04, 0xD2]),
                0x33: bytes([0x65]),
                0x42: bytes([0x37, 0x8C]),  # 14.22 V
                0x46: bytes([0x3A]),
                0x49: bytes([0x00]),
                0x51: bytes([0x01]),  # gasoline
                0x5C: bytes([0x82]),  # 90 °C
                0x5E: bytes([0x00, 0x14]),  # 1.0 L/h
                0x62: bytes([0x8C]),  # 15 %
                0x63: bytes([0x01, 0x2C]),  # 300 Nm
                0xA6: bytes([0x00, 0x1E, 0x84, 0x80]),  # 200000.0 km
            },
            "7E9": {0x0D: bytes([0x00])},
        },
        stored_dtcs=["P0420"],
        pending_dtcs=["P0171"],
    )


def demo_electric_vehicle() -> SimulatedVehicle:
    """A Hyundai/Kia 64 kWh style EV using the hyundai_kia_ev profile layout."""
    bms = wican_payload(
        62,
        {
            10: 0xB4,  # SOC 90 %
            15: 0x80 | 0x20,  # charging + AC plug
            17: 0xFF, 18: 0x9C,  # -10.0 A (charging)
            19: 0x0F, 20: 0x5A,  # 393.0 V
            21: 0x19, 22: 0x16,  # max 25 °C / min 22 °C
            38: 0x8F,  # 14.3 V aux
            49: 0x00, 50: 0x00, 51: 0x7A, 52: 0x12,  # 3123.4 kWh charged
            53: 0x00, 54: 0x00, 55: 0x75, 57: 0x30,  # 3000.0 kWh discharged
        },
        bytes([0x62, 0x01, 0x01]),
    )
    soh = wican_payload(
        46,
        {34: 0x03, 35: 0xE8, 41: 0xB6},  # SOH 100.0 %, display SOC 91 %
        bytes([0x62, 0x01, 0x05]),
    )
    odo = wican_payload(
        15,
        {12: 0x00, 13: 0x9C, 14: 0x40},  # 40000 km
        bytes([0x62, 0xB0, 0x02]),
    )
    return SimulatedVehicle(
        vin="KMHK581GFLU000007",
        mode01={
            "7E8": {
                0x01: bytes([0x00, 0x00, 0x00, 0x00]),
                0x0D: bytes([0x00]),
                0x42: bytes([0x37, 0x50]),
                0x46: bytes([0x3C]),
                0x51: bytes([0x08]),  # electric
            }
        },
        extended={
            ("7E4", "220101"): ("7EC", bms),
            ("7E4", "220105"): ("7EC", soh),
            ("7C6", "22B002"): ("7CE", odo),
        },
    )


class ElmSimulator:
    """Answers ELM327 commands like a v2.x chip connected to ``vehicle``."""

    def __init__(self, vehicle: SimulatedVehicle) -> None:
        self.vehicle = vehicle
        self.echo = True
        self.headers = False
        self.spaces = True
        self.header = "7DF"
        self.priority = "18"
        self.protocol_locked: str | None = None
        self.protocol_found = False
        self.requests: list[str] = []
        # Fault injection
        self.silent_commands: set[str] = set()  # never answer (timeout)
        self.corrupt_next = False
        self.drop_consecutive = False

    # ---------------------------------------------------------- formatting
    @property
    def _is_29bit(self) -> bool:
        return (self.protocol_locked or self.vehicle.protocol) in ("7", "9")

    def _fmt(self, sender: str, data: bytes) -> str:
        hexes = [f"{b:02X}" for b in data]
        if not self.headers:
            return (" " if self.spaces else "").join(hexes[1:])  # PCI hidden without headers
        if self.spaces:
            return " ".join([sender, *hexes])
        return sender + "".join(hexes)

    def _frames(self, sender: str, payload: bytes) -> list[str]:
        pad = bytes([0x55] * 8)
        if len(payload) <= 7:
            frame = bytes([len(payload)]) + payload
            return [self._fmt(sender, (frame + pad)[:8])]
        frames = [bytes([0x10 | (len(payload) >> 8), len(payload) & 0xFF]) + payload[:6]]
        seq = 1
        for start in range(6, len(payload), 7):
            frames.append(bytes([0x20 | seq]) + payload[start : start + 7])
            seq = (seq + 1) & 0x0F
        if self.drop_consecutive and len(frames) > 2:
            del frames[2]
            self.drop_consecutive = False
        return [self._fmt(sender, (f + pad)[:8]) for f in frames]

    # ------------------------------------------------------------ handling
    def handle(self, raw: str) -> str | None:
        cmd = raw.strip().replace(" ", "").upper()
        self.requests.append(cmd)
        if cmd in self.silent_commands:
            return None
        lines = self._handle(cmd)
        if self.corrupt_next and lines:
            lines = [lines[0][:-3] + "Z?"]
            self.corrupt_next = False
        prefix = [raw.strip()] if self.echo else []
        return "\r".join(prefix + lines) + "\r\r>"

    def _handle(self, cmd: str) -> list[str]:
        if cmd.startswith("AT"):
            return self._at(cmd[2:])
        if not self.vehicle.ignition_on:
            if not self.protocol_locked or not self.protocol_found:
                return ["SEARCHING...", "UNABLE TO CONNECT"]
            return ["NO DATA"]
        self.protocol_found = True
        try:
            return self._obd(cmd)
        except (ValueError, IndexError):
            return ["?"]

    def _at(self, cmd: str) -> list[str]:
        if cmd in ("Z", "WS"):
            self.echo, self.headers, self.spaces = True, False, True
            self.header, self.protocol_locked, self.protocol_found = "7DF", None, False
            return ["", "ELM327 v2.3"]
        if cmd in ("E0", "E1"):
            self.echo = cmd == "E1"
            return ["OK"]
        if cmd in ("H0", "H1"):
            self.headers = cmd == "H1"
            return ["OK"]
        if cmd in ("S0", "S1"):
            self.spaces = cmd == "S1"
            return ["OK"]
        if cmd == "I":
            return ["ELM327 v2.3"]
        if cmd == "@1":
            return ["OBDII to RS232 Interpreter"]
        if cmd == "RV":
            return [f"{self.vehicle.battery_voltage:.1f}V"]
        if cmd == "DPN":
            proto = self.protocol_locked or self.vehicle.protocol
            return [proto if self.protocol_locked else f"A{proto}"]
        if cmd.startswith("SP"):
            value = cmd[2:].lstrip("A")
            self.protocol_locked = None if value == "0" else value
            return ["OK"]
        if cmd.startswith("SH"):
            self.header = cmd[2:]
            return ["OK"]
        if cmd.startswith("CP"):
            self.priority = cmd[2:]
            return ["OK"]
        if cmd[:2] in ("L0", "L1", "AT", "ST", "AR", "M0") or cmd.startswith(
            ("CRA", "FCSH", "FCSD", "FCSM", "CAF", "CFC", "CEA", "D")
        ):
            return ["OK"]
        return ["?"]

    def _full_header(self) -> str:
        if self._is_29bit and len(self.header) == 6:
            return self.priority + self.header
        return self.header

    def _obd(self, cmd: str) -> list[str]:
        header = self._full_header()
        if (header, cmd) in self.vehicle.extended:
            sender, payload = self.vehicle.extended[(header, cmd)]
            return self._frames(sender, payload)
        functional = header in ("7DF", "18DB33F1")
        if not functional:
            return ["NO DATA"]
        service = int(cmd[:2], 16)
        out: list[str] = []
        if service == 0x01:
            pids = [int(cmd[i : i + 2], 16) for i in range(2, len(cmd), 2)]
            for ecu, values in self.vehicle.mode01.items():
                data = bytearray([0x41])
                if len(pids) > 1 and not self.vehicle.multi_pid:
                    pids = pids[:1]
                for pid in pids:
                    if pid % 0x20 == 0:
                        bitmap = 0
                        for sup in values:
                            if pid < sup <= pid + 0x20:
                                bitmap |= 1 << (32 - (sup - pid))
                        if any(p > pid + 0x20 for p in values):
                            bitmap |= 1
                        if bitmap or pid == 0:
                            data += bytes([pid]) + bitmap.to_bytes(4, "big")
                    elif pid in values:
                        data += bytes([pid]) + values[pid]
                if len(data) > 1:
                    out.extend(self._frames(self._resp_id(ecu), bytes(data)))
        elif service in (0x03, 0x07, 0x0A):
            codes = {
                0x03: self.vehicle.stored_dtcs,
                0x07: self.vehicle.pending_dtcs,
                0x0A: self.vehicle.permanent_dtcs,
            }[service]
            payload = bytes([service + 0x40, len(codes)]) + b"".join(_dtc_bytes(c) for c in codes)
            out.extend(self._frames(self._resp_id("7E8"), payload))
        elif service == 0x09 and cmd == "0902" and self.vehicle.vin:
            payload = bytes([0x49, 0x02, 0x01]) + self.vehicle.vin.encode()
            out.extend(self._frames(self._resp_id("7E8"), payload))
        elif service == 0x09 and cmd == "0900":
            out.extend(self._frames(self._resp_id("7E8"), bytes([0x49, 0x00, 0x55, 0x40, 0x00, 0x00])))
        elif service == 0x02 and cmd == "020200":
            code = self.vehicle.stored_dtcs[0] if self.vehicle.stored_dtcs else None
            dtc = _dtc_bytes(code) if code else b"\x00\x00"
            out.extend(self._frames(self._resp_id("7E8"), bytes([0x42, 0x02, 0x00]) + dtc))
        return out or ["NO DATA"]

    def _resp_id(self, ecu: str) -> str:
        if self._is_29bit:
            # 7E8 -> 18DAF110 style physical response ids
            return f"18DAF1{int(ecu, 16) - 0x7E8 + 0x10:02X}"
        return ecu


class SimulatedTransport(Transport):
    """A transport that talks to an :class:`ElmSimulator` in-process."""

    kind = "simulator"

    def __init__(
        self,
        simulator: ElmSimulator,
        *,
        chunk_size: int = 20,
        latency: float = 0.0,
        available: bool = True,
    ) -> None:
        super().__init__()
        self.simulator = simulator
        self.chunk_size = chunk_size
        self.latency = latency
        self.available = available
        self._connected = False
        self._pending = ""
        self.connect_count = 0

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self, timeout: float) -> None:
        if not self.available:
            raise TransportUnavailable("Simulated adapter out of range")
        self._connected = True
        self.connect_count += 1

    async def disconnect(self) -> None:
        self._connected = False

    def drop_link(self) -> None:
        """Simulate an unexpected BLE disconnect."""
        self._connected = False
        self._emit_disconnect()

    async def write(self, data: bytes) -> None:
        if not self._connected:
            raise TransportError("Not connected")
        self._pending += data.decode("ascii")
        while "\r" in self._pending:
            cmd, self._pending = self._pending.split("\r", 1)
            if not cmd.strip():
                # bare CR: ELM repeats last command / answers with prompt
                self._schedule(">")
                continue
            response = self.simulator.handle(cmd)
            if response is not None:
                self._schedule(response)

    def _schedule(self, response: str) -> None:
        loop = asyncio.get_running_loop()
        raw = response.encode("ascii")
        chunks = [raw[i : i + self.chunk_size] for i in range(0, len(raw), self.chunk_size)]
        delay = self.latency
        for chunk in chunks:
            loop.call_later(delay, self._emit_data, chunk)
            delay += 0.0005
