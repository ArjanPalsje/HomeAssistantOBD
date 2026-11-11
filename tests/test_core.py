"""Tests for the OBD core library (protocol, ELM327, PIDs, DTC, VIN, formulas).

The core package has no Home Assistant dependency; it is imported as ``core``
(see ``pythonpath`` in pyproject.toml).
"""

from __future__ import annotations

import datetime as dt

import pytest

from core.dtc import decode_dtc_payload, format_dtc
from core.elm327 import ELM327, validate_command
from core.exceptions import (
    AdapterTimeout,
    ForbiddenCommand,
    NoDataError,
    ResponseParseError,
    VehicleUnreachable,
)
from core.expression import Formula, to_wican_bytes
from core.pids import PIDS, decode_support_bitmap
from core.protocol import BusKind, assemble_messages
from core.simulator import (
    ElmSimulator,
    SimulatedTransport,
    demo_combustion_vehicle,
    wican_payload,
)
from core.vin import decode_vin, parse_vin_payload

# ------------------------------------------------------------------ protocol


def test_single_frame_with_padding() -> None:
    msgs = assemble_messages(["7E8064100BE3FA813"], BusKind.CAN_11)
    assert msgs[0].ecu == "7E8"
    assert msgs[0].data == bytes.fromhex("4100BE3FA813")


def test_multi_frame_vin_isotp() -> None:
    # Real-world layout: first frame + 2 consecutive frames, padded to 8 bytes
    lines = [
        "7E81014490201575657",  # FF: len 0x14, 49 02 01 'WVW'
        "7E8215A5A5A41555A48",  # CF1: 'ZZZAUZH'
        "7E82257303030303432",  # CF2: 'W000042'
    ]
    msgs = assemble_messages(lines, BusKind.CAN_11)
    assert len(msgs) == 1
    assert len(msgs[0].data) == 0x14
    assert parse_vin_payload(msgs[0].data) == "WVWZZZAUZHW000042"


def test_multiple_ecus_and_corrupt_one() -> None:
    lines = [
        "7E80641000000000155",
        "7E9 21 FF",  # consecutive frame without first frame -> corrupt
    ]
    msgs = assemble_messages(lines, BusKind.CAN_11)
    assert [m.ecu for m in msgs] == ["7E8"]


def test_only_corrupt_raises() -> None:
    with pytest.raises(ResponseParseError):
        assemble_messages(["7E8 1"], BusKind.CAN_11)


def test_incomplete_multiframe_is_rejected() -> None:
    with pytest.raises(ResponseParseError):
        assemble_messages(["7E81014490201574457"], BusKind.CAN_11)


def test_29bit_and_legacy_frames() -> None:
    msgs = assemble_messages(["18DAF110034105 7B"], BusKind.CAN_29)
    assert msgs[0].ecu == "18DAF110" and msgs[0].data == bytes.fromhex("41057B")
    legacy = assemble_messages(["48 6B 10 41 0D 32 AA"], BusKind.LEGACY)
    assert legacy[0].data == bytes.fromhex("410D32")


# ------------------------------------------------------------- read-only policy


@pytest.mark.parametrize(
    "command",
    ["04", "14FFFFFF", "1003", "1101", "2EF19000", "2F", "3101FF00", "2701", "ATPP0CSV69", "ATMA", "ATBD", "AT KW"],
)
def test_write_and_dangerous_commands_are_blocked(command: str) -> None:
    with pytest.raises(ForbiddenCommand):
        validate_command(command)


@pytest.mark.parametrize(
    "command",
    ["0100", "010C0D", "03", "07", "0A", "0902", "020200", "220101", "2101", "ATSH7E4", "ATFCSH17FC007B", "ATCRA7EC", "ATSP7", "AT RV"],
)
def test_read_commands_are_allowed(command: str) -> None:
    validate_command(command)


# -------------------------------------------------------------------- ELM327


async def _elm(vehicle=None, **kwargs):
    sim = ElmSimulator(vehicle or demo_combustion_vehicle())
    transport = SimulatedTransport(sim, **kwargs)
    await transport.connect(1)
    elm = ELM327(transport, command_timeout=0.5)
    await elm.initialize()
    return elm, sim, transport


async def test_initialize_and_protocol_detection() -> None:
    elm, sim, _ = await _elm()
    assert elm.info.identity == "ELM327 v2.3"
    assert not sim.echo and sim.headers and not sim.spaces
    info = await elm.detect_protocol()
    assert info.protocol_number == "6"
    assert info.bus is BusKind.CAN_11
    assert sim.protocol_locked == "6"


async def test_query_and_battery_voltage() -> None:
    elm, _, _ = await _elm()
    await elm.detect_protocol()
    msgs = await elm.query("010C")
    assert msgs[0].data == bytes.fromhex("410C0BB8")
    assert await elm.read_adapter_voltage() == 12.6


async def test_ignition_off_is_unreachable() -> None:
    vehicle = demo_combustion_vehicle()
    vehicle.ignition_on = False
    elm, _, _ = await _elm(vehicle)
    with pytest.raises(VehicleUnreachable):
        await elm.detect_protocol()


async def test_no_data_after_vehicle_switched_off() -> None:
    vehicle = demo_combustion_vehicle()
    elm, _, _ = await _elm(vehicle)
    await elm.detect_protocol()
    vehicle.ignition_on = False
    with pytest.raises(NoDataError):
        await elm.query("010C")


async def test_timeout_and_resync() -> None:
    elm, sim, _ = await _elm()
    await elm.detect_protocol()
    sim.silent_commands.add("0105")
    with pytest.raises(AdapterTimeout):
        await elm.query("0105", timeout=0.2)
    sim.silent_commands.clear()
    msgs = await elm.query("010D")  # adapter recovers after resync
    assert msgs[0].data == bytes.fromhex("410D00")


async def test_corrupt_response_raises_parse_error() -> None:
    elm, sim, _ = await _elm()
    await elm.detect_protocol()
    sim.corrupt_next = True
    with pytest.raises(ResponseParseError):
        await elm.query("010C")


async def test_concurrent_commands_are_serialised() -> None:
    import asyncio

    elm, _, _ = await _elm(latency=0.01)
    await elm.detect_protocol()
    results = await asyncio.gather(*(elm.query(f"01{pid:02X}") for pid in (0x0C, 0x0D, 0x05, 0x2F)))
    assert [r[0].data[1] for r in results] == [0x0C, 0x0D, 0x05, 0x2F]


async def test_forbidden_query_never_reaches_adapter() -> None:
    elm, sim, _ = await _elm()
    await elm.detect_protocol()
    with pytest.raises(ForbiddenCommand):
        await elm.query("04")
    assert "04" not in sim.requests


async def test_small_ble_chunks_are_reassembled() -> None:
    elm, _, _ = await _elm(chunk_size=3)
    await elm.detect_protocol()
    msgs = await elm.query("0902")
    assert parse_vin_payload(msgs[0].data) == "WVWZZZAUZHW000042"


# ----------------------------------------------------------- PIDs / DTC / VIN


def test_pid_formulas() -> None:
    assert PIDS[0x0C].decode(bytes([0x1A, 0xF8])) == {"rpm": 1726}
    assert PIDS[0x05].decode(bytes([0x7B])) == {"coolant_temp": 83}
    assert PIDS[0x2F].decode(bytes([0xFF])) == {"fuel_level": 100.0}
    assert PIDS[0x42].decode(bytes([0x37, 0x8C])) == {"control_module_voltage": 14.22}
    assert PIDS[0xA6].decode(bytes([0x00, 0x1E, 0x84, 0x80])) == {"odometer": 200000.0}
    assert PIDS[0x01].decode(bytes([0x83, 0, 0, 0])) == {"mil_on": True, "dtc_count_ecu": 3}
    assert PIDS[0x51].decode(bytes([0x04])) == {"fuel_type": "diesel"}


def test_support_bitmap() -> None:
    assert decode_support_bitmap(0x00, bytes.fromhex("BE3FA813")) >= {0x01, 0x03, 0x04, 0x0C, 0x0D, 0x20}


def test_dtc_decoding() -> None:
    assert format_dtc(0x01, 0x33) == "P0133"
    assert format_dtc(0xC1, 0x23) == "U0123"
    assert decode_dtc_payload(bytes.fromhex("4302013300420000"), can=True) == ["P0133", "P0042"]
    assert decode_dtc_payload(bytes.fromhex("43013300000000"), can=False) == ["P0133"]


def test_vin_decoding() -> None:
    info = decode_vin("KMHK581GFLU000007", today=dt.date(2026, 11, 11))
    assert info.valid and info.manufacturer == "Hyundai" and info.model_year == 2020
    assert not decode_vin("INVALIDVIN").valid


# ------------------------------------------------------------------ formulas


def test_wican_layout_roundtrip() -> None:
    payload = wican_payload(62, {10: 0xB4, 57: 0x30, 18: 0x9C}, bytes([0x62, 0x01, 0x01]))
    raw = to_wican_bytes(payload)
    assert raw[2] == 0x62 and raw[8] == 0x21 and raw[16] == 0x22
    assert raw[10] == 0xB4 and raw[57] == 0x30 and raw[18] == 0x9C
    assert Formula("B10/2").evaluate(raw) == 90


def test_formula_syntax() -> None:
    raw = bytes([0x05, 0x62, 0x01, 0x01, 0xFF, 0x9C, 0x80])
    assert Formula("[B4:B5]").evaluate(raw) == 0xFF9C
    assert Formula("[S4:S5]").evaluate(raw) == -100
    assert Formula("S4").evaluate(raw) == -1
    assert Formula("B6:7").evaluate(raw) == 1
    assert Formula("((S4*256)+B5)/10").evaluate(raw) == -10.0
    assert Formula("max(0, 0-x)").evaluate(values={"x": -3}) == 3


@pytest.mark.parametrize(
    "expr", ["__import__('os')", "open('x')", "B4.__class__", "[x for x in B4]", "lambda: 1", "2**99999999"]
)
def test_formula_rejects_code(expr: str) -> None:
    with pytest.raises(ValueError):
        Formula(expr)


def test_formula_short_response() -> None:
    with pytest.raises(ResponseParseError):
        Formula("B20").evaluate(bytes(5))
