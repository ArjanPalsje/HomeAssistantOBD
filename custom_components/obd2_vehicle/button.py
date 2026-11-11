"""Buttons for manual actions. None of them writes anything to the vehicle."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import OBDConfigEntry, OBDVehicleCoordinator
from .entity import OBDEntity

PARALLEL_UPDATES = 0


async def _full_read(coordinator: OBDVehicleCoordinator) -> None:
    coordinator.async_request_read(full=True)


@dataclass(frozen=True, kw_only=True)
class OBDButtonDescription(ButtonEntityDescription):
    press_fn: Callable[[OBDVehicleCoordinator], Coroutine[Any, Any, None]]


BUTTONS: tuple[OBDButtonDescription, ...] = (
    OBDButtonDescription(
        key="read_now",
        translation_key="read_now",
        icon="mdi:car-search",
        press_fn=_full_read,
    ),
    OBDButtonDescription(
        key="reconnect",
        translation_key="reconnect",
        icon="mdi:bluetooth-connect",
        entity_category=EntityCategory.DIAGNOSTIC,
        press_fn=lambda c: c.async_reconnect(),
    ),
    OBDButtonDescription(
        key="reidentify",
        translation_key="reidentify",
        icon="mdi:car-info",
        entity_category=EntityCategory.DIAGNOSTIC,
        press_fn=lambda c: c.async_reidentify(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OBDConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(OBDButton(entry.runtime_data, d) for d in BUTTONS)


class OBDButton(OBDEntity, ButtonEntity):
    entity_description: OBDButtonDescription

    def __init__(self, coordinator: OBDVehicleCoordinator, description: OBDButtonDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        await self.entity_description.press_fn(self.coordinator)
