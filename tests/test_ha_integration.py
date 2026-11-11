"""Home Assistant level tests (config flow, setup, entities) using the simulator.

Requires ``pytest-homeassistant-custom-component`` (Linux/macOS, see CI). Skipped
automatically when it is not installed.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.data_entry_flow import FlowResultType  # noqa: E402
from homeassistant.helpers import entity_registry as er  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.obd2_vehicle.const import (  # noqa: E402
    CONF_CONNECTION,
    CONF_MODE,
    CONF_PROFILE,
    CONF_SIM_VEHICLE,
    CONF_VEHICLE_NAME,
    CONN_SIMULATOR,
    DOMAIN,
)
from custom_components.obd2_vehicle.diagnostics import (  # noqa: E402
    async_get_config_entry_diagnostics,
)


@pytest.fixture(autouse=True)
def auto_enable(enable_custom_integrations, request):  # noqa: ANN001
    if sys.platform != "win32":  # HA's Bluetooth stack does not run on Windows
        request.getfixturevalue("enable_bluetooth")
    yield


async def _wait_for(predicate, timeout: float = 10.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.05)


async def test_config_flow_simulator(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "simulator"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_SIM_VEHICLE: "electric"})
    assert result["step_id"] == "vehicle"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_VEHICLE_NAME: "Kona", CONF_PROFILE: "auto", CONF_MODE: "snapshot"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CONNECTION] == CONN_SIMULATOR
    await hass.async_block_till_done()


async def _setup(hass: HomeAssistant, kind: str, name: str) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=name,
        unique_id=f"simulator_{kind}_{name}",
        data={CONF_CONNECTION: CONN_SIMULATOR, CONF_SIM_VEHICLE: kind, CONF_VEHICLE_NAME: name},
        options={CONF_MODE: "snapshot", CONF_PROFILE: "auto"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_two_vehicles_read_independently(hass: HomeAssistant) -> None:
    petrol = await _setup(hass, "combustion", "Golf")
    ev = await _setup(hass, "electric", "Kona")
    await _wait_for(lambda: petrol.runtime_data.data.last_read and ev.runtime_data.data.last_read)
    await _wait_for(
        lambda: hass.states.get("sensor.kona_hv_battery_state_of_charge_bms") is not None
        and hass.states.get("sensor.golf_fuel_level") is not None
        and petrol.runtime_data.data.dtcs is not None,
        timeout=30,
    )
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    golf_entities = er.async_entries_for_config_entry(registry, petrol.entry_id)
    kona_entities = er.async_entries_for_config_entry(registry, ev.entry_id)
    assert golf_entities and kona_entities

    assert hass.states.get("sensor.golf_fuel_level").state == "50.2"
    assert hass.states.get("sensor.golf_stored_fault_codes").attributes["codes"] == ["P0420"]
    assert hass.states.get("sensor.kona_hv_battery_state_of_charge_bms").state == "90.0"
    assert hass.states.get("sensor.kona_odometer").state == "40000"

    # Snapshot mode: link closed afterwards -> realtime values unavailable,
    # state values keep their last known value.
    await _wait_for(lambda: not petrol.runtime_data.data.connected)
    petrol.runtime_data.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.golf_engine_speed").state == "unavailable"
    fuel = hass.states.get("sensor.golf_fuel_level")
    assert fuel.state == "50.2" and fuel.attributes["data_status"] == "last_known"


async def test_diagnostics_are_redacted(hass: HomeAssistant) -> None:
    entry = await _setup(hass, "combustion", "Golf")
    await _wait_for(lambda: entry.runtime_data.identity.vin is not None, timeout=30)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    text = str(diag)
    assert "WVWZZZAUZHW000042" not in text
    assert diag["runtime"]["vehicle"]["vin_present"] is True
