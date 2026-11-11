"""Parsing of ELM327 output into vehicle messages.

The ELM327 is always run with headers ON (``ATH1``) and spaces OFF (``ATS0``),
so every line is ``<header><data>``. For CAN (ISO 15765-4) the data includes
the ISO-TP PCI byte; multi-frame responses (VIN, DTC lists, UDS responses) are
reassembled here (ISO-TP first frame / consecutive frames). The adapter takes
care of sending flow-control frames.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from .exceptions import ResponseParseError

_LOGGER = logging.getLogger(__name__)


class BusKind(Enum):
    """How response lines are laid out."""

    CAN_11 = "can11"
    CAN_29 = "can29"
    LEGACY = "legacy"  # SAE J1850, ISO 9141-2, ISO 14230-4 (KWP2000)


#: ELM327 protocol numbers (ATDPN) -> (description, bus kind)
ELM_PROTOCOLS: dict[str, tuple[str, BusKind]] = {
    "1": ("SAE J1850 PWM", BusKind.LEGACY),
    "2": ("SAE J1850 VPW", BusKind.LEGACY),
    "3": ("ISO 9141-2", BusKind.LEGACY),
    "4": ("ISO 14230-4 KWP (5 baud init)", BusKind.LEGACY),
    "5": ("ISO 14230-4 KWP (fast init)", BusKind.LEGACY),
    "6": ("ISO 15765-4 CAN (11 bit, 500 kbaud)", BusKind.CAN_11),
    "7": ("ISO 15765-4 CAN (29 bit, 500 kbaud)", BusKind.CAN_29),
    "8": ("ISO 15765-4 CAN (11 bit, 250 kbaud)", BusKind.CAN_11),
    "9": ("ISO 15765-4 CAN (29 bit, 250 kbaud)", BusKind.CAN_29),
}


@dataclass(slots=True)
class Frame:
    """A single line returned by the adapter."""

    sender: str
    data: bytes


@dataclass(slots=True)
class Message:
    """A complete (reassembled) diagnostic response from one ECU.

    ``data`` starts with the response service id, e.g. ``41 0C 1A F8``.
    """

    ecu: str
    data: bytes

    @property
    def is_negative(self) -> bool:
        return len(self.data) >= 3 and self.data[0] == 0x7F

    @property
    def nrc(self) -> int | None:
        return self.data[2] if self.is_negative else None


def _hex_to_bytes(text: str) -> bytes:
    if len(text) % 2:
        raise ResponseParseError(f"Odd number of hex digits: {text!r}")
    try:
        return bytes.fromhex(text)
    except ValueError as err:
        raise ResponseParseError(f"Invalid hex in response: {text!r}") from err


def parse_frame(line: str, kind: BusKind) -> Frame:
    """Split one adapter output line into sender + data."""
    text = line.replace(" ", "").strip().upper()
    if kind is BusKind.CAN_11:
        if len(text) < 5:
            raise ResponseParseError(f"CAN frame too short: {line!r}")
        return Frame(sender=text[:3], data=_hex_to_bytes(text[3:]))
    if kind is BusKind.CAN_29:
        if len(text) < 10:
            raise ResponseParseError(f"CAN frame too short: {line!r}")
        return Frame(sender=text[:8], data=_hex_to_bytes(text[8:]))
    # Legacy: 3 header bytes (priority, target, source), data, checksum.
    raw = _hex_to_bytes(text)
    if len(raw) < 5:
        raise ResponseParseError(f"Legacy frame too short: {line!r}")
    return Frame(sender=f"{raw[2]:02X}", data=raw[3:-1])


def _assemble_can(frames: list[Frame], errors: list[str]) -> list[Message]:
    messages: list[Message] = []
    broken: set[str] = set()
    # sender -> [expected_length, buffer, next_sequence]
    pending: dict[str, list] = {}
    for frame in frames:
        if not frame.data or frame.sender in broken:
            continue
        try:
            _handle_can_frame(frame, messages, pending)
        except ResponseParseError as err:
            # Corrupt data from one ECU must not discard answers of other ECUs.
            errors.append(str(err))
            broken.add(frame.sender)
            pending.pop(frame.sender, None)
    for sender, state in pending.items():
        errors.append(
            f"Incomplete multi-frame response from {sender} ({len(state[1])}/{state[0]} bytes)"
        )
    return messages


def _handle_can_frame(
    frame: Frame, messages: list[Message], pending: dict[str, list]
) -> None:
    pci = frame.data[0] >> 4
    if pci == 0x0:  # single frame
        length = frame.data[0] & 0x0F
        if length == 0 and len(frame.data) > 1:  # CAN-FD escape
            length = frame.data[1]
            payload = frame.data[2 : 2 + length]
        else:
            payload = frame.data[1 : 1 + length]
        if len(payload) < length:
            raise ResponseParseError(
                f"Single frame from {frame.sender} truncated ({len(payload)}/{length})"
            )
        messages.append(Message(frame.sender, bytes(payload)))
        return
    if pci == 0x1:  # first frame
        if len(frame.data) < 2:
            raise ResponseParseError("First frame too short")
        length = ((frame.data[0] & 0x0F) << 8) | frame.data[1]
        pending[frame.sender] = [length, bytearray(frame.data[2:]), 1]
    elif pci == 0x2:  # consecutive frame
        state = pending.get(frame.sender)
        if state is None:
            # Consecutive frame without first frame -> corrupt / lost frame
            raise ResponseParseError(
                f"Consecutive frame from {frame.sender} without first frame"
            )
        seq = frame.data[0] & 0x0F
        if seq != state[2]:
            raise ResponseParseError(
                f"ISO-TP sequence error from {frame.sender}: got {seq}, expected {state[2]}"
            )
        state[1].extend(frame.data[1:])
        state[2] = (state[2] + 1) & 0x0F
        if len(state[1]) >= state[0]:
            messages.append(Message(frame.sender, bytes(state[1][: state[0]])))
            del pending[frame.sender]
    elif pci == 0x3:  # flow control (echo of our own FC) -> ignore
        return
    else:
        raise ResponseParseError(f"Unknown ISO-TP PCI {frame.data[0]:02X}")


def _assemble_legacy(frames: list[Frame]) -> list[Message]:
    """Combine legacy (non CAN) frames.

    Mode 09 responses are split in numbered lines ``49 02 <seq> d d d d``;
    DTC responses are split in lines ``43 d d d d d d``. Lines from the same
    ECU with the same response service (and PID for mode 09) are merged.
    """
    grouped: dict[tuple[str, int, int | None], list[bytes]] = {}
    order: list[tuple[str, int, int | None]] = []
    for frame in frames:
        if not frame.data:
            continue
        service = frame.data[0]
        pid = frame.data[1] if service in (0x49,) and len(frame.data) > 1 else None
        key = (frame.sender, service, pid)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(frame.data)
    messages: list[Message] = []
    for key in order:
        parts = grouped[key]
        sender, service, pid = key
        if len(parts) == 1:
            messages.append(Message(sender, parts[0]))
        elif service == 0x49:
            parts.sort(key=lambda p: p[2] if len(p) > 2 else 0)
            data = bytearray(parts[0][:2])
            data.append(len(parts))
            for part in parts:
                data.extend(part[3:])
            messages.append(Message(sender, bytes(data)))
        elif service in (0x43, 0x47, 0x4A):
            data = bytearray([service])
            for part in parts:
                data.extend(part[1:])
            messages.append(Message(sender, bytes(data)))
        else:
            messages.extend(Message(sender, p) for p in parts)
    return messages


def assemble_messages(lines: list[str], kind: BusKind) -> list[Message]:
    """Turn adapter output lines into complete messages.

    Corrupt lines or broken multi-frame sequences of one ECU are skipped (and
    logged); a :class:`ResponseParseError` is only raised when nothing usable
    is left.
    """
    errors: list[str] = []
    frames: list[Frame] = []
    for line in lines:
        try:
            frames.append(parse_frame(line, kind))
        except ResponseParseError as err:
            errors.append(str(err))
    if kind is BusKind.LEGACY:
        messages = _assemble_legacy(frames)
    else:
        messages = _assemble_can(frames, errors)
    if errors:
        if not messages:
            raise ResponseParseError("; ".join(errors))
        _LOGGER.debug("Ignored corrupt response parts: %s", "; ".join(errors))
    return messages


def select_positive(messages: list[Message], service: int) -> list[Message]:
    """Return the positive responses for a request with service id ``service``.

    Negative responses with NRC 0x78 ("response pending") are dropped, other
    negative responses are dropped as well (caller can inspect the raw list).
    """
    expected = service + 0x40
    return [m for m in messages if m.data and m.data[0] == expected]


#: UDS / KWP negative response codes (ISO 14229-1) used for log messages.
NEGATIVE_RESPONSE_CODES: dict[int, str] = {
    0x10: "generalReject",
    0x11: "serviceNotSupported",
    0x12: "subFunctionNotSupported",
    0x13: "incorrectMessageLengthOrInvalidFormat",
    0x14: "responseTooLong",
    0x21: "busyRepeatRequest",
    0x22: "conditionsNotCorrect",
    0x24: "requestSequenceError",
    0x31: "requestOutOfRange",
    0x33: "securityAccessDenied",
    0x78: "requestCorrectlyReceived-ResponsePending",
    0x7E: "subFunctionNotSupportedInActiveSession",
    0x7F: "serviceNotSupportedInActiveSession",
}
