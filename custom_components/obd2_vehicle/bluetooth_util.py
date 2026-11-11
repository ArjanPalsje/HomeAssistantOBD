"""Bluetooth helpers: choosing a specific adapter / ESPHome proxy.

Home Assistant's Bluetooth client wrapper normally picks the connection path
itself (best RSSI with a free connection slot) and ignores which ``BLEDevice``
was passed in. To honour the user's adapter choice we subclass the wrapper
that Home Assistant installs as ``bleak_retry_connector.BleakClientWithServiceCache``
and let it only consider advertisements seen by the chosen scanner ``source``
(adapter MAC address / proxy source).
"""

from __future__ import annotations

from typing import Any

import bleak_retry_connector
from bleak.backends.device import BLEDevice

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

AUTO_SOURCE = "auto"


class _SourceFilteredManager:
    """Proxy for the Bluetooth manager that only returns one scanner's devices."""

    def __init__(self, manager: Any, source: str) -> None:
        self._manager = manager
        self._source = source

    def async_scanner_devices_by_address(self, address: str, connectable: bool) -> list[Any]:
        return [
            device
            for device in self._manager.async_scanner_devices_by_address(address, connectable)
            if device.scanner.source == self._source
        ]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._manager, name)


def pinned_client_class(source: str) -> type:
    """Return a client class that only connects through ``source``.

    Must be called inside Home Assistant (after the Bluetooth integration has
    installed its client wrapper). Falls back to the default class when the
    wrapper does not provide the expected hook, so a Home Assistant update can
    never break connecting – it only loses the pinning (logged by the caller).
    """
    base = bleak_retry_connector.BleakClientWithServiceCache
    if not hasattr(base, "_async_get_best_available_backend_and_device"):
        return base

    class PinnedClient(base):  # type: ignore[misc, valid-type]
        def _async_get_best_available_backend_and_device(self, manager: Any) -> Any:
            return super()._async_get_best_available_backend_and_device(
                _SourceFilteredManager(manager, source)
            )

    PinnedClient.__name__ = f"PinnedClient[{source}]"
    return PinnedClient


def pinning_supported() -> bool:
    return hasattr(
        bleak_retry_connector.BleakClientWithServiceCache,
        "_async_get_best_available_backend_and_device",
    )


def ble_device_via_source(hass: HomeAssistant, address: str, source: str) -> BLEDevice | None:
    """BLEDevice as seen by one specific adapter/proxy (None when not seen)."""
    for device in bluetooth.async_scanner_devices_by_address(hass, address, connectable=True):
        if device.scanner.source == source:
            return device.ble_device
    return None


def rssi_via_source(hass: HomeAssistant, address: str, source: str) -> int | None:
    for device in bluetooth.async_scanner_devices_by_address(hass, address, connectable=True):
        if device.scanner.source == source:
            return device.advertisement.rssi
    return None


def adapter_options(hass: HomeAssistant, address: str | None) -> list[dict[str, str]]:
    """Selectable connectable scanners, with the RSSI at which they see the adapter."""
    seen: dict[str, int] = {}
    if address:
        for device in bluetooth.async_scanner_devices_by_address(hass, address, connectable=True):
            seen[device.scanner.source] = device.advertisement.rssi
    options: list[dict[str, str]] = []
    for scanner in bluetooth.async_current_scanners(hass):
        if not getattr(scanner, "connectable", False):
            continue
        source = scanner.source
        name = getattr(scanner, "name", source)
        rssi = f"{seen[source]} dBm" if source in seen else "–"
        options.append({"value": source, "label": f"{name} ({source}) · {rssi}"})
    options.sort(key=lambda o: o["label"])
    return options
