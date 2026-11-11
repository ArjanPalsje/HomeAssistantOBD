"""Bluetooth Low Energy transport (bleak + bleak-retry-connector).

Works with local adapters and ESPHome Bluetooth proxies alike, because the
``BLEDevice`` is supplied by Home Assistant's Bluetooth stack, which routes the
connection through whichever scanner has the best path to the device.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    establish_connection,
)

from ..adapters import GATT_LAYOUTS, IGNORED_SERVICES
from ..exceptions import TransportError, TransportUnavailable
from .base import Transport

_LOGGER = logging.getLogger(__name__)

DEFAULT_CHUNK = 20  # safe write size for the default 23 byte ATT MTU


@dataclass(slots=True)
class ResolvedLayout:
    """Characteristics actually used on this device."""

    name: str
    service: str
    notify: BleakGATTCharacteristic
    write: BleakGATTCharacteristic
    write_with_response: bool


class BleTransport(Transport):
    """ELM327 byte stream over BLE GATT notify/write characteristics."""

    kind = "ble"

    def __init__(
        self,
        device_provider: Callable[[], BLEDevice | None],
        name: str,
        *,
        preferred_layout: str | None = None,
    ) -> None:
        super().__init__()
        self._device_provider = device_provider
        self._name = name
        self._preferred = preferred_layout
        self._client: BleakClient | None = None
        self._layouts: list[ResolvedLayout] = []
        self._active: int = -1
        self._expected_disconnect = False
        self._services_dump: list[dict[str, object]] = []

    # ---------------------------------------------------------------- state
    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    @property
    def active_layout(self) -> str | None:
        if 0 <= self._active < len(self._layouts):
            return self._layouts[self._active].name
        return None

    @property
    def has_more_layouts(self) -> bool:
        return self._active + 1 < len(self._layouts)

    # ------------------------------------------------------------- connect
    async def connect(self, timeout: float) -> None:
        device = self._device_provider()
        if device is None:
            raise TransportUnavailable(f"{self._name} is not in range of any Bluetooth adapter/proxy")
        self._expected_disconnect = False
        try:
            async with asyncio.timeout(timeout):
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    device,
                    self._name,
                    disconnected_callback=self._on_disconnected,
                    max_attempts=3,
                    ble_device_callback=self._device_provider,
                )
        except TimeoutError as err:
            raise TransportUnavailable(f"Timeout connecting to {self._name}") from err
        except (BleakError, OSError) as err:
            raise TransportUnavailable(f"Cannot connect to {self._name}: {err}") from err
        self._client = client
        try:
            self._resolve_layouts(client)
            if not self._layouts:
                raise TransportError(
                    "No serial GATT service found; this device is probably not an "
                    "ELM327 BLE adapter (see diagnostics for the service list)"
                )
            start = 0
            if self._preferred:
                for idx, layout in enumerate(self._layouts):
                    if layout.name == self._preferred:
                        start = idx
            await self._activate(start)
        except (BleakError, TransportError) as err:
            await self.disconnect()
            if isinstance(err, TransportError):
                raise
            raise TransportError(f"GATT setup failed: {err}") from err

    def _resolve_layouts(self, client: BleakClient) -> None:
        self._layouts = []
        self._services_dump = []
        services = client.services
        for service in services:
            self._services_dump.append(
                {
                    "service": service.uuid,
                    "characteristics": [
                        {"uuid": c.uuid, "properties": list(c.properties)}
                        for c in service.characteristics
                    ],
                }
            )
        # 1) Known layouts in priority order
        for layout in GATT_LAYOUTS:
            service = services.get_service(layout.service)
            if service is None:
                continue
            notify = service.get_characteristic(layout.notify)
            write = service.get_characteristic(layout.write)
            if notify is None or write is None:
                continue
            if not {"notify", "indicate"} & set(notify.properties):
                continue
            self._layouts.append(self._make(layout.name, service.uuid, notify, write))
        known = {layout.service for layout in self._layouts}
        # 2) Heuristic: any vendor service with notify + write characteristics
        for service in services:
            if service.uuid in IGNORED_SERVICES or service.uuid in known:
                continue
            notify = next(
                (c for c in service.characteristics if {"notify", "indicate"} & set(c.properties)),
                None,
            )
            write = next(
                (
                    c
                    for c in service.characteristics
                    if {"write", "write-without-response"} & set(c.properties)
                ),
                None,
            )
            if notify and write:
                self._layouts.append(
                    self._make(f"Detected {service.uuid}", service.uuid, notify, write)
                )

    @staticmethod
    def _make(
        name: str,
        service: str,
        notify: BleakGATTCharacteristic,
        write: BleakGATTCharacteristic,
    ) -> ResolvedLayout:
        with_response = "write-without-response" not in write.properties
        return ResolvedLayout(name, service, notify, write, with_response)

    async def _activate(self, index: int) -> None:
        assert self._client is not None
        if 0 <= self._active < len(self._layouts):
            with contextlib.suppress(BleakError, ValueError):
                await self._client.stop_notify(self._layouts[self._active].notify)
        layout = self._layouts[index]
        await self._client.start_notify(layout.notify, self._on_notify)
        self._active = index
        _LOGGER.debug(
            "%s: using GATT layout '%s' (service %s, notify %s, write %s, response=%s)",
            self._name,
            layout.name,
            layout.service,
            layout.notify.uuid,
            layout.write.uuid,
            layout.write_with_response,
        )

    async def next_layout(self) -> bool:
        """Switch to the next candidate layout. Returns False when exhausted."""
        if not self.has_more_layouts or self._client is None:
            return False
        try:
            await self._activate(self._active + 1)
        except BleakError as err:
            raise TransportError(f"Switching GATT layout failed: {err}") from err
        return True

    # ----------------------------------------------------------- callbacks
    def _on_notify(self, _char: BleakGATTCharacteristic, data: bytearray) -> None:
        self._emit_data(bytes(data))

    def _on_disconnected(self, _client: BleakClient) -> None:
        if self._expected_disconnect:
            return
        _LOGGER.debug("%s: BLE link lost", self._name)
        self._emit_disconnect()

    # --------------------------------------------------------------- write
    async def write(self, data: bytes) -> None:
        if self._client is None or not self.is_connected or self._active < 0:
            raise TransportError("Not connected")
        layout = self._layouts[self._active]
        size = DEFAULT_CHUNK
        with contextlib.suppress(Exception):
            size = max(DEFAULT_CHUNK, min(layout.write.max_write_without_response_size, 244))
        try:
            for start in range(0, len(data), size):
                await self._client.write_gatt_char(
                    layout.write, data[start : start + size], response=layout.write_with_response
                )
        except (BleakError, OSError) as err:
            raise TransportError(f"BLE write failed: {err}") from err

    async def disconnect(self) -> None:
        self._expected_disconnect = True
        client, self._client = self._client, None
        self._active = -1
        if client is not None:
            with contextlib.suppress(Exception):
                await client.disconnect()

    def diagnostics(self) -> dict[str, object]:
        return {
            **super().diagnostics(),
            "active_layout": self.active_layout,
            "candidate_layouts": [layout.name for layout in self._layouts],
            "gatt_services": self._services_dump,
        }
