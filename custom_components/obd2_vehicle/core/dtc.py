"""Diagnostic trouble code decoding (SAE J2012 / ISO 15031-6)."""

from __future__ import annotations

_LETTERS = "PCBU"


def format_dtc(high: int, low: int) -> str:
    """Two raw bytes -> 'P0123'."""
    letter = _LETTERS[high >> 6]
    return f"{letter}{(high >> 4) & 0x03}{high & 0x0F:X}{low:02X}"


def decode_dtc_payload(payload: bytes, *, can: bool) -> list[str]:
    """Decode a Mode 03/07/0A response payload (starting with 0x43/0x47/0x4A).

    On CAN the second byte is the number of DTCs; legacy protocols pad
    the list with 0x0000 entries instead.
    """
    if not payload:
        return []
    body = payload[1:]
    count: int | None = None
    if can and body:
        count = body[0]
        body = body[1:]
    codes: list[str] = []
    for idx in range(0, len(body) - 1, 2):
        high, low = body[idx], body[idx + 1]
        if high == 0 and low == 0:
            continue
        codes.append(format_dtc(high, low))
    if count is not None:
        codes = codes[:count]
    # Preserve order but remove duplicates (multiple ECUs may report the same)
    return list(dict.fromkeys(codes))
