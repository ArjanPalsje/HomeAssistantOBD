"""Binary sensor platform."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import OBDConfigEntry, OBDVehicleCoordinator
from .core.specs import ValueSpec
from .entity import OBDEntity, OBDValueMixin, entity_category, localised_name

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class OBDBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[OBDVehicleCoordinator], bool | None]


VEHICLE_BINARY_SENSORS: tuple[OBDBinaryDescription, ...] = (
    OBDBinaryDescription(
        key="adapter_present",
        translation_key="adapter_present",
        device_class=BinarySensorDeviceClass.PRESENCE,
        value_fn=lambda c: c.data.adapter_present,
    ),
    OBDBinaryDescription(
        key="connected",
        translation_key="connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.data.connected,
    ),
    OBDBinaryDescription(
        key="vehicle_responding",
        translation_key="vehicle_responding",
        device_class=BinarySensorDeviceClass.POWER,
        value_fn=lambda c: c.data.vehicle_responding,
    ),
    OBDBinaryDescription(
        key="engine_running",
        translation_key="engine_running",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda c: (
            bool(c.data.values.get("rpm")) if c.data.session_active and "rpm" in c.data.live_keys
            else (False if c.data.vehicle_responding is False else None)
        ),
    ),
    OBDBinaryDescription(
        key="fault_codes_present",
        translation_key="fault_codes_present",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda c: (
            None
            if c.data.dtcs is None
            else bool(c.data.dtcs.stored or c.data.dtcs.pending or c.data.dtcs.permanent)
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OBDConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    language = hass.config.language
    entities: list[BinarySensorEntity] = [
        OBDVehicleBinarySensor(coordinator, d) for d in VEHICLE_BINARY_SENSORS
    ]
    created: set[str] = set()

    def build(specs: list[ValueSpec]) -> list[BinarySensorEntity]:
        new: list[BinarySensorEntity] = []
        for spec in specs:
            if not spec.binary or spec.key in created:
                continue
            created.add(spec.key)
            new.append(OBDValueBinarySensor(coordinator, spec, language))
        return new

    entities.extend(build(coordinator.known_specs()))
    async_add_entities(entities)

    @callback
    def _new_specs(specs: list[ValueSpec]) -> None:
        if new := build(specs):
            async_add_entities(new)

    entry.async_on_unload(coordinator.async_add_spec_listener(_new_specs))


class OBDVehicleBinarySensor(OBDEntity, BinarySensorEntity):
    entity_description: OBDBinaryDescription

    def __init__(self, coordinator: OBDVehicleCoordinator, description: OBDBinaryDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.coordinator)


class OBDValueBinarySensor(OBDValueMixin, OBDEntity, BinarySensorEntity):
    """Binary value decoded from the vehicle (MIL, charging, plug …)."""

    def __init__(self, coordinator: OBDVehicleCoordinator, spec: ValueSpec, language: str) -> None:
        super().__init__(coordinator, spec.key)
        self._spec = spec
        if spec.translation_key:
            self._attr_translation_key = spec.translation_key
        else:
            self._attr_name = localised_name(spec, language)
        self._attr_device_class = (
            BinarySensorDeviceClass(spec.device_class) if spec.device_class else None
        )
        self._attr_entity_category = entity_category(spec)
        self._attr_entity_registry_enabled_default = spec.enabled_default
        if spec.icon:
            self._attr_icon = spec.icon

    @property
    def available(self) -> bool:
        return super().available and self._value_available()

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.data.values.get(self._spec.key)
        return None if value is None else bool(value)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return self._value_attributes()
