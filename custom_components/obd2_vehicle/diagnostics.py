"""Diagnostics download. VIN, Bluetooth address and host are redacted."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_ADDRESS, CONF_HOST
from .coordinator import OBDConfigEntry

TO_REDACT = {CONF_ADDRESS, CONF_HOST, "vin", "unique_id", "address"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: OBDConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(
            {"data": dict(entry.data), "options": dict(entry.options), "unique_id": entry.unique_id},
            TO_REDACT,
        ),
        "runtime": async_redact_data(coordinator.diagnostics(), TO_REDACT),
    }
