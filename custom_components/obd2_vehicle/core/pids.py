"""SAE J1979 / ISO 15031-5 Mode 01 PID definitions.

Only standardised PIDs with their published scaling are listed here. Each PID
decodes into one or more named values (see :class:`ValueSpec`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .specs import CAT_EMISSIONS, CAT_ENGINE, CAT_EV, CAT_GENERAL, ValueSpec

Decoder = Callable[[bytes], dict[str, object]]


@dataclass(frozen=True, slots=True)
class PidDef:
    """A Mode 01 PID."""

    pid: int
    length: int
    decode: Decoder
    values: tuple[ValueSpec, ...]


def _u8(data: bytes) -> int:
    return data[0]


def _u16(data: bytes) -> int:
    return (data[0] << 8) | data[1]


def _single(key: str, func: Callable[[bytes], object]) -> Decoder:
    return lambda data: {key: func(data)}


def _pct(data: bytes) -> float:
    return round(data[0] * 100 / 255, 1)


def _temp(data: bytes) -> int:
    return data[0] - 40


def _trim(data: bytes) -> float:
    return round((data[0] - 128) * 100 / 128, 1)


#: PID 0x51 – fuel type coding (SAE J1979 table)
FUEL_TYPES: dict[int, str] = {
    0: "not_available",
    1: "gasoline",
    2: "methanol",
    3: "ethanol",
    4: "diesel",
    5: "lpg",
    6: "cng",
    7: "propane",
    8: "electric",
    9: "bifuel_gasoline",
    10: "bifuel_methanol",
    11: "bifuel_ethanol",
    12: "bifuel_lpg",
    13: "bifuel_cng",
    14: "bifuel_propane",
    15: "bifuel_electricity",
    16: "bifuel_electric_combustion",
    17: "hybrid_gasoline",
    18: "hybrid_ethanol",
    19: "hybrid_diesel",
    20: "hybrid_electric",
    21: "hybrid_electric_combustion",
    22: "hybrid_regenerative",
    23: "bifuel_diesel",
}


def _monitor_status(data: bytes) -> dict[str, object]:
    return {
        "mil_on": bool(data[0] & 0x80),
        "dtc_count_ecu": data[0] & 0x7F,
    }


def _v(key: str, **kwargs) -> ValueSpec:
    kwargs.setdefault("name", key.replace("_", " ").capitalize())
    return ValueSpec(key=key, translation_key=key, **kwargs)


_PID_LIST: list[PidDef] = [
    PidDef(
        0x01,
        4,
        _monitor_status,
        (
            _v("mil_on", name="Malfunction indicator lamp", binary=True, device_class="problem",
               state_class=None, category=CAT_GENERAL),
            _v("dtc_count_ecu", name="DTC count (reported by ECU)", icon="mdi:engine-off",
               state_class=None, category=CAT_GENERAL),
        ),
    ),
    PidDef(0x04, 1, _single("engine_load", _pct),
           (_v("engine_load", name="Engine load", unit="%", icon="mdi:engine", realtime=True,
               precision=1, category=CAT_ENGINE),)),
    PidDef(0x05, 1, _single("coolant_temp", _temp),
           (_v("coolant_temp", name="Coolant temperature", unit="°C", device_class="temperature",
               realtime=True, category=CAT_ENGINE),)),
    PidDef(0x06, 1, _single("short_fuel_trim_1", _trim),
           (_v("short_fuel_trim_1", name="Short term fuel trim bank 1", unit="%", realtime=True,
               category=CAT_EMISSIONS, enabled_default=False, precision=1),)),
    PidDef(0x07, 1, _single("long_fuel_trim_1", _trim),
           (_v("long_fuel_trim_1", name="Long term fuel trim bank 1", unit="%", realtime=True,
               category=CAT_EMISSIONS, enabled_default=False, precision=1),)),
    PidDef(0x08, 1, _single("short_fuel_trim_2", _trim),
           (_v("short_fuel_trim_2", name="Short term fuel trim bank 2", unit="%", realtime=True,
               category=CAT_EMISSIONS, enabled_default=False, precision=1),)),
    PidDef(0x09, 1, _single("long_fuel_trim_2", _trim),
           (_v("long_fuel_trim_2", name="Long term fuel trim bank 2", unit="%", realtime=True,
               category=CAT_EMISSIONS, enabled_default=False, precision=1),)),
    PidDef(0x0A, 1, _single("fuel_pressure", lambda d: d[0] * 3),
           (_v("fuel_pressure", name="Fuel pressure", unit="kPa", device_class="pressure",
               realtime=True, category=CAT_ENGINE, enabled_default=False),)),
    PidDef(0x0B, 1, _single("intake_pressure", _u8),
           (_v("intake_pressure", name="Intake manifold pressure", unit="kPa",
               device_class="pressure", realtime=True, category=CAT_ENGINE),)),
    PidDef(0x0C, 2, _single("rpm", lambda d: round(_u16(d) / 4)),
           (_v("rpm", name="Engine speed", unit="rpm", icon="mdi:gauge", realtime=True,
               category=CAT_ENGINE),)),
    PidDef(0x0D, 1, _single("speed", _u8),
           (_v("speed", name="Vehicle speed", unit="km/h", device_class="speed", realtime=True,
               category=CAT_GENERAL),)),
    PidDef(0x0E, 1, _single("timing_advance", lambda d: d[0] / 2 - 64),
           (_v("timing_advance", name="Timing advance", unit="°", realtime=True,
               category=CAT_ENGINE, enabled_default=False, precision=1),)),
    PidDef(0x0F, 1, _single("intake_temp", _temp),
           (_v("intake_temp", name="Intake air temperature", unit="°C",
               device_class="temperature", realtime=True, category=CAT_ENGINE),)),
    PidDef(0x10, 2, _single("maf", lambda d: round(_u16(d) / 100, 2)),
           (_v("maf", name="Mass air flow", unit="g/s", icon="mdi:weather-windy",
               realtime=True, category=CAT_ENGINE, precision=2),)),
    PidDef(0x11, 1, _single("throttle_position", _pct),
           (_v("throttle_position", name="Throttle position", unit="%", icon="mdi:speedometer",
               realtime=True, category=CAT_ENGINE, precision=1),)),
    PidDef(0x1F, 2, _single("run_time", _u16),
           (_v("run_time", name="Run time since engine start", unit="s",
               device_class="duration", realtime=True, category=CAT_ENGINE),)),
    PidDef(0x21, 2, _single("distance_with_mil", _u16),
           (_v("distance_with_mil", name="Distance travelled with MIL on", unit="km",
               device_class="distance", state_class="total", category=CAT_GENERAL,
               entity_category="diagnostic"),)),
    PidDef(0x23, 2, _single("fuel_rail_pressure", lambda d: _u16(d) * 10),
           (_v("fuel_rail_pressure", name="Fuel rail gauge pressure", unit="kPa",
               device_class="pressure", realtime=True, category=CAT_ENGINE,
               enabled_default=False),)),
    PidDef(0x2C, 1, _single("commanded_egr", _pct),
           (_v("commanded_egr", name="Commanded EGR", unit="%", realtime=True,
               category=CAT_EMISSIONS, enabled_default=False, precision=1),)),
    PidDef(0x2F, 1, _single("fuel_level", _pct),
           (_v("fuel_level", name="Fuel level", unit="%", icon="mdi:gas-station",
               category=CAT_GENERAL, precision=1),)),
    PidDef(0x30, 1, _single("warmups_since_clear", _u8),
           (_v("warmups_since_clear", name="Warm-ups since codes cleared", state_class=None,
               category=CAT_EMISSIONS, entity_category="diagnostic", enabled_default=False),)),
    PidDef(0x31, 2, _single("distance_since_clear", _u16),
           (_v("distance_since_clear", name="Distance since codes cleared", unit="km",
               device_class="distance", state_class="total", category=CAT_GENERAL,
               entity_category="diagnostic"),)),
    PidDef(0x33, 1, _single("barometric_pressure", _u8),
           (_v("barometric_pressure", name="Barometric pressure", unit="kPa",
               device_class="atmospheric_pressure", realtime=True, category=CAT_ENGINE,
               enabled_default=False),)),
    PidDef(0x3C, 2, _single("catalyst_temp_b1s1", lambda d: round(_u16(d) / 10 - 40, 1)),
           (_v("catalyst_temp_b1s1", name="Catalyst temperature bank 1 sensor 1", unit="°C",
               device_class="temperature", realtime=True, category=CAT_EMISSIONS,
               enabled_default=False, precision=1),)),
    PidDef(0x42, 2, _single("control_module_voltage", lambda d: round(_u16(d) / 1000, 2)),
           (_v("control_module_voltage", name="Control module voltage (12 V system)", unit="V",
               device_class="voltage", realtime=True, category=CAT_GENERAL, precision=2),)),
    PidDef(0x43, 2, _single("absolute_load", lambda d: round(_u16(d) * 100 / 255, 1)),
           (_v("absolute_load", name="Absolute load", unit="%", realtime=True,
               category=CAT_ENGINE, enabled_default=False, precision=1),)),
    PidDef(0x44, 2, _single("commanded_lambda", lambda d: round(_u16(d) * 2 / 65536, 3)),
           (_v("commanded_lambda", name="Commanded air-fuel equivalence ratio", realtime=True,
               category=CAT_EMISSIONS, enabled_default=False, precision=3),)),
    PidDef(0x45, 1, _single("relative_throttle", _pct),
           (_v("relative_throttle", name="Relative throttle position", unit="%", realtime=True,
               category=CAT_ENGINE, enabled_default=False, precision=1),)),
    PidDef(0x46, 1, _single("ambient_temp", _temp),
           (_v("ambient_temp", name="Ambient air temperature", unit="°C",
               device_class="temperature", realtime=True, category=CAT_GENERAL),)),
    PidDef(0x49, 1, _single("accelerator_pedal_d", _pct),
           (_v("accelerator_pedal_d", name="Accelerator pedal position D", unit="%",
               realtime=True, category=CAT_ENGINE, precision=1),)),
    PidDef(0x4A, 1, _single("accelerator_pedal_e", _pct),
           (_v("accelerator_pedal_e", name="Accelerator pedal position E", unit="%",
               realtime=True, category=CAT_ENGINE, enabled_default=False, precision=1),)),
    PidDef(0x4C, 1, _single("commanded_throttle", _pct),
           (_v("commanded_throttle", name="Commanded throttle actuator", unit="%",
               realtime=True, category=CAT_ENGINE, enabled_default=False, precision=1),)),
    PidDef(0x4D, 2, _single("time_with_mil", _u16),
           (_v("time_with_mil", name="Time run with MIL on", unit="min", device_class="duration",
               state_class="total", category=CAT_GENERAL, entity_category="diagnostic",
               enabled_default=False),)),
    PidDef(0x4E, 2, _single("time_since_clear", _u16),
           (_v("time_since_clear", name="Time since codes cleared", unit="min",
               device_class="duration", state_class="total", category=CAT_GENERAL,
               entity_category="diagnostic", enabled_default=False),)),
    PidDef(0x51, 1, _single("fuel_type", lambda d: FUEL_TYPES.get(d[0], "not_available")),
           (_v("fuel_type", name="Fuel type", device_class="enum", state_class=None,
               options=tuple(dict.fromkeys(FUEL_TYPES.values())), icon="mdi:fuel",
               category=CAT_GENERAL, entity_category="diagnostic"),)),
    PidDef(0x52, 1, _single("ethanol_percent", _pct),
           (_v("ethanol_percent", name="Ethanol fuel percentage", unit="%", category=CAT_ENGINE,
               enabled_default=False, precision=1),)),
    PidDef(0x5A, 1, _single("relative_accelerator", _pct),
           (_v("relative_accelerator", name="Relative accelerator pedal position", unit="%",
               realtime=True, category=CAT_ENGINE, enabled_default=False, precision=1),)),
    PidDef(0x5B, 1, _single("hybrid_battery_remaining", _pct),
           (_v("hybrid_battery_remaining", name="Hybrid/EV battery pack remaining life",
               unit="%", device_class="battery", category=CAT_EV, precision=1),)),
    PidDef(0x5C, 1, _single("oil_temp", _temp),
           (_v("oil_temp", name="Engine oil temperature", unit="°C", device_class="temperature",
               realtime=True, category=CAT_ENGINE),)),
    PidDef(0x5E, 2, _single("fuel_rate", lambda d: round(_u16(d) / 20, 2)),
           (_v("fuel_rate", name="Engine fuel rate", unit="L/h", icon="mdi:fuel", realtime=True,
               category=CAT_ENGINE, precision=2),)),
    PidDef(0x61, 1, _single("driver_demand_torque", lambda d: d[0] - 125),
           (_v("driver_demand_torque", name="Driver demand engine torque", unit="%",
               realtime=True, category=CAT_ENGINE, enabled_default=False),)),
    PidDef(0x62, 1, _single("actual_torque_pct", lambda d: d[0] - 125),
           (_v("actual_torque_pct", name="Actual engine torque", unit="%", realtime=True,
               category=CAT_ENGINE, enabled_default=False),)),
    PidDef(0x63, 2, _single("reference_torque", _u16),
           (_v("reference_torque", name="Engine reference torque", unit="N·m",
               state_class=None, category=CAT_ENGINE, entity_category="diagnostic",
               enabled_default=False),)),
    PidDef(0xA6, 4, _single("odometer",
                            lambda d: round(((d[0] << 24) | (d[1] << 16) | (d[2] << 8) | d[3]) / 10, 1)),
           (_v("odometer", name="Odometer", unit="km", device_class="distance",
               state_class="total_increasing", icon="mdi:counter", category=CAT_GENERAL,
               precision=1),)),
]

PIDS: dict[int, PidDef] = {p.pid: p for p in _PID_LIST}

#: Values computed from multiple PIDs (handled by the reader).
DERIVED_VALUES: tuple[ValueSpec, ...] = (
    ValueSpec(
        key="engine_torque",
        name="Engine torque",
        translation_key="engine_torque",
        unit="N·m",
        realtime=True,
        category=CAT_ENGINE,
        icon="mdi:engine",
    ),
)

#: PIDs used to decide whether the engine is running.
RPM_PID = 0x0C

SUPPORT_PIDS = (0x00, 0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0)


def decode_support_bitmap(base: int, bitmap: bytes) -> set[int]:
    """Decode a 4 byte 'PIDs supported [base+1 - base+0x20]' bitmap."""
    supported: set[int] = set()
    value = int.from_bytes(bitmap[:4], "big")
    for bit in range(32):
        if value & (1 << (31 - bit)):
            supported.add(base + bit + 1)
    return supported


def all_value_specs() -> list[ValueSpec]:
    """All value specs defined by standard PIDs."""
    specs = [spec for pid in _PID_LIST for spec in pid.values]
    specs.extend(DERIVED_VALUES)
    return specs
