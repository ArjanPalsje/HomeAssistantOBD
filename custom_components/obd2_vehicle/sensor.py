"""Sensor platform."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricPotential,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONN_BLE, CONNECTION_STATES
from .coordinator import OBDConfigEntry, OBDVehicleCoordinator
from .core.specs import ValueSpec
from .entity import OBDEntity, OBDValueMixin, entity_category, localised_name

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class OBDSensorDescription(SensorEntityDescription):
    value_fn: Callable[[OBDVehicleCoordinator], Any]
    attr_fn: Callable[[OBDVehicleCoordinator], dict[str, Any] | None] = lambda _: None
    available_fn: Callable[[OBDVehicleCoordinator], bool] = lambda _: True
    ble_only: bool = False
    advanced_only: bool = False


def _dtc_attrs(kind: str) -> Callable[[OBDVehicleCoordinator], dict[str, Any] | None]:
    def attrs(coord: OBDVehicleCoordinator) -> dict[str, Any] | None:
        dtcs = coord.data.dtcs
        if dtcs is None:
            return None
        result: dict[str, Any] = {"codes": getattr(dtcs, kind) or []}
        if kind == "stored" and dtcs.freeze_frame_dtc:
            result["freeze_frame_dtc"] = dtcs.freeze_frame_dtc
        return result

    return attrs


def _dtc_count(kind: str) -> Callable[[OBDVehicleCoordinator], int | None]:
    def value(coord: OBDVehicleCoordinator) -> int | None:
        dtcs = coord.data.dtcs
        if dtcs is None or getattr(dtcs, kind) is None:
            return None
        return len(getattr(dtcs, kind))

    return value


def _profile_name(coord: OBDVehicleCoordinator) -> str | None:
    profile_id = coord.identity.profile_id
    return coord.registry.get(profile_id).name if profile_id else None


VEHICLE_SENSORS: tuple[OBDSensorDescription, ...] = (
    OBDSensorDescription(
        key="connection_state",
        translation_key="connection_state",
        device_class=SensorDeviceClass.ENUM,
        options=CONNECTION_STATES,
        value_fn=lambda c: c.data.connection_state,
    ),
    OBDSensorDescription(
        key="last_read",
        translation_key="last_read",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda c: c.data.last_read,
    ),
    OBDSensorDescription(
        key="last_full_read",
        translation_key="last_full_read",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.data.last_full_read,
    ),
    OBDSensorDescription(
        key="last_connected",
        translation_key="last_connected",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.data.last_connected,
    ),
    OBDSensorDescription(
        key="adapter_voltage",
        translation_key="adapter_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=1,
        value_fn=lambda c: c.data.adapter_voltage,
    ),
    OBDSensorDescription(
        key="dtc_stored",
        translation_key="dtc_stored",
        icon="mdi:car-wrench",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_dtc_count("stored"),
        attr_fn=_dtc_attrs("stored"),
    ),
    OBDSensorDescription(
        key="dtc_pending",
        translation_key="dtc_pending",
        icon="mdi:car-clock",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_dtc_count("pending"),
        attr_fn=_dtc_attrs("pending"),
    ),
    OBDSensorDescription(
        key="dtc_permanent",
        translation_key="dtc_permanent",
        icon="mdi:car-wrench",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_dtc_count("permanent"),
        attr_fn=_dtc_attrs("permanent"),
        advanced_only=True,
    ),
    OBDSensorDescription(
        key="vin",
        translation_key="vin",
        icon="mdi:card-account-details-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.identity.vin,
    ),
    OBDSensorDescription(
        key="protocol",
        translation_key="protocol",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.data.protocol,
    ),
    OBDSensorDescription(
        key="adapter",
        translation_key="adapter",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.data.adapter_identity,
    ),
    OBDSensorDescription(
        key="vehicle_profile",
        translation_key="vehicle_profile",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_profile_name,
    ),
    OBDSensorDescription(
        key="supported_pids",
        translation_key="supported_pids",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: len(c.identity.supported_pids) if c.identity.protocol else None,
        attr_fn=lambda c: {"pids": [f"{p:02X}" for p in sorted(c.identity.supported_pids)]},
    ),
    OBDSensorDescription(
        key="last_error",
        translation_key="last_error",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: (c.data.last_error or "")[:255] or None,
    ),
    OBDSensorDescription(
        key="rssi",
        translation_key="rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: c.data.rssi,
        ble_only=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OBDConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    language = hass.config.language
    entities: list[SensorEntity] = [
        OBDVehicleSensor(coordinator, description)
        for description in VEHICLE_SENSORS
        if (not description.ble_only or coordinator.connection == CONN_BLE)
        and (not description.advanced_only or coordinator.advanced)
    ]
    created: set[str] = set()

    def build(specs: list[ValueSpec]) -> list[SensorEntity]:
        new: list[SensorEntity] = []
        for spec in specs:
            if spec.binary or spec.key in created:
                continue
            created.add(spec.key)
            new.append(OBDValueSensor(coordinator, spec, language))
        return new

    entities.extend(build(coordinator.known_specs()))
    async_add_entities(entities)

    @callback
    def _new_specs(specs: list[ValueSpec]) -> None:
        if new := build(specs):
            async_add_entities(new)

    entry.async_on_unload(coordinator.async_add_spec_listener(_new_specs))


class OBDVehicleSensor(OBDEntity, SensorEntity):
    """Vehicle / adapter level sensor."""

    entity_description: OBDSensorDescription

    def __init__(self, coordinator: OBDVehicleCoordinator, description: OBDSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return self.entity_description.attr_fn(self.coordinator)


class OBDValueSensor(OBDValueMixin, OBDEntity, SensorEntity):
    """A measured vehicle value."""

    def __init__(self, coordinator: OBDVehicleCoordinator, spec: ValueSpec, language: str) -> None:
        super().__init__(coordinator, spec.key)
        self._spec = spec
        if spec.translation_key:
            self._attr_translation_key = spec.translation_key
        else:
            self._attr_name = localised_name(spec, language)
        self._attr_native_unit_of_measurement = spec.unit
        self._attr_device_class = SensorDeviceClass(spec.device_class) if spec.device_class else None
        self._attr_state_class = SensorStateClass(spec.state_class) if spec.state_class else None
        self._attr_suggested_display_precision = spec.precision
        self._attr_entity_category = entity_category(spec)
        self._attr_entity_registry_enabled_default = spec.enabled_default
        if spec.icon:
            self._attr_icon = spec.icon
        if spec.options:
            self._attr_options = list(spec.options)

    @property
    def available(self) -> bool:
        return super().available and self._value_available()

    @property
    def native_value(self) -> Any:
        value = self.coordinator.data.values.get(self._spec.key)
        if self._spec.options and value not in self._spec.options:
            return None
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._value_attributes()

