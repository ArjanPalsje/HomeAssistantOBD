"""OBD2 Vehicle – Bluetooth/Wi-Fi OBD2 diagnostics for Home Assistant."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.util.hass_dict import HassKey

from .const import DOMAIN
from .coordinator import OBDConfigEntry, OBDVehicleCoordinator
from .core.profiles import ProfileRegistry

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]

REGISTRY_KEY: HassKey[ProfileRegistry] = HassKey(f"{DOMAIN}_profiles")
_REGISTRY_LOCK: HassKey[asyncio.Lock] = HassKey(f"{DOMAIN}_profiles_lock")


async def async_get_registry(hass: HomeAssistant) -> ProfileRegistry:
    """Load the vehicle profiles once (file I/O runs in the executor)."""
    lock = hass.data.setdefault(_REGISTRY_LOCK, asyncio.Lock())
    async with lock:
        if REGISTRY_KEY not in hass.data:
            registry = ProfileRegistry()
            await hass.async_add_executor_job(registry.load_directory)
            hass.data[REGISTRY_KEY] = registry
    return hass.data[REGISTRY_KEY]


async def async_setup_entry(hass: HomeAssistant, entry: OBDConfigEntry) -> bool:
    """Set up one adapter / vehicle."""
    registry = await async_get_registry(hass)
    coordinator = OBDVehicleCoordinator(hass, entry, registry)
    entry.runtime_data = coordinator
    await coordinator.async_setup()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: OBDConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: OBDConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: OBDConfigEntry) -> None:
    """Delete persisted data (VIN, values) when the entry is removed."""
    from homeassistant.helpers.storage import Store

    from .const import STORAGE_VERSION

    await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()
