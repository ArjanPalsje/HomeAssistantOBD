"""Base entity."""

from __future__ import annotations

from homeassistant.const import EntityCategory
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_ADDRESS, CONN_BLE, DOMAIN
from .coordinator import OBDVehicleCoordinator
from .core.specs import ValueSpec


class OBDEntity(CoordinatorEntity[OBDVehicleCoordinator]):
    """One device per vehicle; every entity belongs to it."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: OBDVehicleCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        connections: set[tuple[str, str]] = set()
        if coordinator.connection == CONN_BLE and entry.data.get(CONF_ADDRESS):
            connections.add((dr.CONNECTION_BLUETOOTH, entry.data[CONF_ADDRESS]))
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            connections=connections,
            name=coordinator.vehicle_name,
        )


def entity_category(spec: ValueSpec) -> EntityCategory | None:
    if spec.entity_category == "diagnostic":
        return EntityCategory.DIAGNOSTIC
    return None


def localised_name(spec: ValueSpec, language: str) -> str | None:
    """Name for profile values (built-in values use translation keys)."""
    if spec.translation_key:
        return None
    lang = language.split("-")[0].lower()
    return spec.translations.get(lang, spec.name)


class OBDValueMixin:
    """Availability rules shared by value sensors and binary sensors.

    * realtime values: only available while a read session with a responding
      vehicle is active – an old rpm must never look like a current rpm.
    * other values: keep the last known value; the ``data_status`` attribute
      tells whether it was read in the current session (``live``) or is the
      ``last_known`` value. The vehicle device also has "last read"
      timestamp sensors.
    """

    coordinator: OBDVehicleCoordinator
    _spec: ValueSpec
    _unrecorded_attributes = frozenset({"data_status", "unavailable_reason"})

    def _value_available(self) -> bool:
        data = self.coordinator.data
        if self._spec.key not in data.values:
            return False
        if self._spec.realtime:
            return data.session_active and self._spec.key in data.live_keys
        return True

    def _value_attributes(self) -> dict[str, object]:
        data = self.coordinator.data
        attrs: dict[str, object] = {
            "data_status": "live"
            if data.session_active and self._spec.key in data.live_keys
            else "last_known"
        }
        reason = self.coordinator.unavailable.get(self._spec.key)
        if reason:
            attrs["unavailable_reason"] = reason
        return attrs
