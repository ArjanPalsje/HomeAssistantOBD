"""End-to-end tests: reader + profiles + scheduler against the simulator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.elm327 import ELM327, validate_command
from core.exceptions import VehicleUnreachable
from core.profiles import PROFILE_DIR, ProfileRegistry
from core.reader import VehicleIdentity, VehicleReader
from core.scheduler import PlannerConfig, PlannerState, ReadMode, backoff_delay, plan
from core.simulator import (
    ElmSimulator,
    SimulatedTransport,
    demo_combustion_vehicle,
    demo_electric_vehicle,
)


@pytest.fixture(scope="module")
def registry() -> ProfileRegistry:
    reg = ProfileRegistry()
    reg.load_directory()
    return reg


async def _reader(vehicle, registry, identity=None, profile="auto"):
    sim = ElmSimulator(vehicle)
    transport = SimulatedTransport(sim)
    await transport.connect(1)
    elm = ELM327(transport, command_timeout=0.5)
    await elm.initialize()
    return VehicleReader(elm, registry, identity, profile_setting=profile), sim


# ------------------------------------------------------------------ profiles


def test_all_profiles_load(registry: ProfileRegistry) -> None:
    assert registry.errors == {}
    files = list(PROFILE_DIR.glob("*.json"))
    assert len(registry.profiles) == len(files) + 1  # + generic


def test_profiles_are_read_only_and_sourced() -> None:
    for path in PROFILE_DIR.glob("*.json"):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        assert data.get("sources") or data.get("extends"), path.name
        for request in data.get("requests", []):
            service = int(request["request"][:2], 16)
            assert service in (0x21, 0x22), f"{path.name}: {request['request']}"
            for cmd in [*request.get("init", []), *data.get("init", [])]:
                assert validate_command(cmd).startswith("AT")


def test_profile_candidates_by_vin(registry: ProfileRegistry) -> None:
    assert [p.id for p in registry.candidates_for_vin("KMHK581GFLU000007")] == [
        "hyundai_kia_ev",
        "hyundai_ioniq_28",
    ]
    assert registry.candidates_for_vin("YV1XXXXXXXX000000") == []
    assert registry.candidates_for_vin(None) == []


# -------------------------------------------------------------------- reader


async def test_combustion_vehicle_full_read(registry: ProfileRegistry) -> None:
    reader, _ = await _reader(demo_combustion_vehicle(), registry)
    await reader.start()
    assert reader.identity.vin == "WVWZZZAUZHW000042"
    # VW WMI -> MEB profile probed, combustion VW does not answer -> generic
    assert reader.profile.id == "generic"
    values = await reader.read_values(full=True)
    assert values["rpm"] == 750
    assert values["fuel_level"] == 50.2
    assert values["odometer"] == 200000.0
    assert values["engine_torque"] == 45
    assert values["mil_on"] is True
    assert reader.identity.multi_pid is True
    dtcs = await reader.read_dtcs(permanent=True, freeze_frame=True)
    assert dtcs.stored == ["P0420"] and dtcs.pending == ["P0171"] and dtcs.permanent == []
    assert dtcs.freeze_frame_dtc == "P0420"


async def test_fast_read_skips_slow_pids(registry: ProfileRegistry) -> None:
    reader, _ = await _reader(demo_combustion_vehicle(), registry)
    await reader.start()
    values = await reader.read_values(full=False)
    assert "rpm" in values and "fuel_level" in values
    assert "odometer" not in values and "fuel_type" not in values


async def test_ecu_without_multi_pid_support(registry: ProfileRegistry) -> None:
    vehicle = demo_combustion_vehicle()
    vehicle.multi_pid = False
    reader, _ = await _reader(vehicle, registry)
    await reader.start()
    values = await reader.read_values(full=True)
    assert values["rpm"] == 750 and values["odometer"] == 200000.0
    assert reader.identity.multi_pid is False


async def test_ev_profile_autodetect_and_values(registry: ProfileRegistry) -> None:
    reader, sim = await _reader(demo_electric_vehicle(), registry)
    await reader.start()
    assert reader.profile.id == "hyundai_kia_ev"
    values = await reader.read_values(full=True)
    assert values["hv_soc"] == 90.0
    assert values["hv_voltage"] == 393.0
    assert values["hv_current"] == -10.0
    assert values["hv_power"] == -3.93
    assert values["charging_power"] == 3.93
    assert values["hv_soh"] == 100.0
    assert values["aux_battery_voltage"] == 14.3
    assert values["odometer"] == 40000
    assert values["energy_discharged_total"] == 3000.0
    # addressing restored to functional after the profile requests
    assert sim.header == "7DF"


async def test_manual_egmp_profile_adds_charging_flags(registry: ProfileRegistry) -> None:
    reader, _ = await _reader(demo_electric_vehicle(), registry, profile="hyundai_kia_egmp")
    await reader.start()
    values = await reader.read_values(full=False)
    assert values["charging"] is True and values["ac_plug"] is True and values["charging_dc"] is False


async def test_lost_consecutive_frame_marks_profile_values_unavailable(registry: ProfileRegistry) -> None:
    reader, sim = await _reader(demo_electric_vehicle(), registry)
    await reader.start()
    sim.drop_consecutive = True
    values = await reader.read_values(full=False)
    assert "hv_soc" not in values
    assert "hv_soc" in reader.unavailable
    # next cycle works again
    values = await reader.read_values(full=False)
    assert values["hv_soc"] == 90.0


async def test_vehicle_off_raises_unreachable(registry: ProfileRegistry) -> None:
    vehicle = demo_combustion_vehicle()
    reader, _ = await _reader(vehicle, registry)
    await reader.start()
    vehicle.ignition_on = False
    with pytest.raises(VehicleUnreachable):
        await reader.read_values(full=False)


async def test_cached_identity_skips_discovery(registry: ProfileRegistry) -> None:
    reader, _ = await _reader(demo_combustion_vehicle(), registry)
    await reader.start()
    stored = VehicleIdentity.from_dict(json.loads(json.dumps(reader.identity.as_dict())))
    reader2, sim2 = await _reader(demo_combustion_vehicle(), registry, stored)
    await reader2.start()
    assert "0120" not in sim2.requests  # bitmaps not read again
    assert (await reader2.read_values(full=False))["rpm"] == 750


async def test_vehicle_change_resets_identity(registry: ProfileRegistry) -> None:
    reader, _ = await _reader(demo_combustion_vehicle(), registry)
    await reader.start()
    reader2, _ = await _reader(demo_electric_vehicle(), registry, reader.identity)
    await reader2.start()
    assert reader2.vehicle_changed
    assert reader2.identity.vin == "KMHK581GFLU000007"
    assert reader2.profile.id == "hyundai_kia_ev"


# ----------------------------------------------------------------- scheduler


def test_scheduler_waits_when_absent() -> None:
    assert plan(PlannerConfig(), PlannerState(present=False), 0).run_in is None


def test_scheduler_snapshot_on_arrival_then_idle() -> None:
    cfg = PlannerConfig(mode=ReadMode.SNAPSHOT, arrival_delay=5)
    st = PlannerState(present=True, arrived_at=100)
    assert plan(cfg, st, 102).run_in == pytest.approx(3)
    first = plan(cfg, st, 106)
    assert first.run_in == 0 and first.full
    st.snapshot_done_for_arrival = True
    assert plan(cfg, st, 200).run_in is None


def test_scheduler_arrival_retries_are_limited() -> None:
    cfg = PlannerConfig(mode=ReadMode.SNAPSHOT, arrival_delay=0, arrival_retries=2, arrival_retry_interval=60)
    st = PlannerState(present=True, arrived_at=0, last_attempt=10, arrival_attempts=1, ecu_awake=False)
    assert plan(cfg, st, 20).run_in == pytest.approx(50)
    st.arrival_attempts = 2
    assert plan(cfg, st, 20).run_in is None  # give up until next arrival


def test_scheduler_smart_mode_intervals() -> None:
    cfg = PlannerConfig(live_interval=10, charging_interval=300, parked_interval=1800, full_read_interval=3600)
    st = PlannerState(present=True, arrived_at=0, snapshot_done_for_arrival=True,
                      last_attempt=1000, last_full=1000, ecu_awake=True)
    st.engine_running = True
    p = plan(cfg, st, 1000)
    assert p.run_in == 10 and p.keep_connected and not p.full
    st.engine_running, st.charging = False, True
    p = plan(cfg, st, 1000)
    assert p.run_in == 300 and not p.keep_connected
    st.charging = False
    assert plan(cfg, st, 1000).run_in == 1800
    st.ecu_awake = False
    assert plan(cfg, st, 1000).run_in == cfg.probe_interval
    cfg.probe_interval = 0
    assert plan(cfg, st, 1000).run_in is None


def test_scheduler_backoff_and_manual() -> None:
    cfg = PlannerConfig(backoff_base=10, backoff_max=100)
    assert [backoff_delay(cfg, n) for n in range(1, 6)] == [10, 20, 40, 80, 100]
    st = PlannerState(present=True, arrived_at=0, last_attempt=50, connect_failures=3)
    assert plan(cfg, st, 60).run_in == pytest.approx(30)
    st.manual_request, st.manual_full = True, True
    p = plan(cfg, st, 60)
    assert p.run_in == 0 and p.full
