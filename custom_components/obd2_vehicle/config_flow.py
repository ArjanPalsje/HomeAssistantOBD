"""Config and options flow.

One config entry = one adapter = one vehicle. Add the integration again for
every additional adapter/vehicle; adapters that are already configured are
hidden from the discovery list.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

from . import async_get_registry
from .bluetooth_util import (
    AUTO_SOURCE,
    adapter_options,
    ble_device_via_source,
    pinned_client_class,
    pinning_supported,
)
from .const import (
    ALL_CATEGORIES,
    CONF_ADAPTER_NAME,
    CONF_ADDRESS,
    CONF_ADVANCED,
    CONF_BT_SOURCE,
    CONF_CATEGORIES,
    CONF_CHARGING_INTERVAL,
    CONF_COMMAND_TIMEOUT,
    CONF_CONNECTION,
    CONF_FULL_INTERVAL,
    CONF_HOST,
    CONF_LIVE_INTERVAL,
    CONF_LOG_TRAFFIC,
    CONF_MODE,
    CONF_PARKED_INTERVAL,
    CONF_PORT,
    CONF_PROBE_INTERVAL,
    CONF_PROFILE,
    CONF_SIM_VEHICLE,
    CONF_VEHICLE_NAME,
    CONN_BLE,
    CONN_SIMULATOR,
    CONN_TCP,
    CONNECT_TIMEOUT,
    DEFAULT_CATEGORIES,
    DEFAULT_CHARGING_INTERVAL,
    DEFAULT_COMMAND_TIMEOUT,
    DEFAULT_FULL_INTERVAL,
    DEFAULT_LIVE_INTERVAL,
    DEFAULT_MODE,
    DEFAULT_PARKED_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_PROBE_INTERVAL,
    DOMAIN,
)
from .core.adapters import identify_adapter, is_known_service
from .core.elm327 import ELM327
from .core.exceptions import AdapterTimeout, OBDError
from .core.reader import AUTO_PROFILE
from .core.scheduler import ReadMode
from .core.transport.base import Transport

_LOGGER = logging.getLogger(__name__)

CONF_SKIP_TEST = "skip_connection_test"


def _is_obd_candidate(info: bluetooth.BluetoothServiceInfoBleak) -> bool:
    return identify_adapter(info.name) is not None or any(
        is_known_service(uuid) for uuid in info.service_uuids
    )


async def _test_adapter(hass: HomeAssistant, transport: Transport) -> None:
    """Connect and initialise the adapter (does not talk to the vehicle)."""
    elm = ELM327(transport, command_timeout=4.0)
    try:
        await transport.connect(CONNECT_TIMEOUT)
        while True:
            try:
                await elm.initialize()
                return
            except AdapterTimeout:
                next_layout = getattr(transport, "next_layout", None)
                if next_layout is None or not await next_layout():
                    raise
    finally:
        with contextlib.suppress(Exception):
            await transport.disconnect()


def _bt_source_selector(
    hass: HomeAssistant, address: str | None, current: str = AUTO_SOURCE
) -> selector.SelectSelector:
    """Choice of Bluetooth adapter / ESPHome proxy (with RSSI to the dongle)."""
    auto_label = (
        "Automatisch (beste signaal met vrij verbindingsslot)"
        if hass.config.language.startswith("nl")
        else "Automatic (best signal with a free connection slot)"
    )
    options = [selector.SelectOptionDict(value=AUTO_SOURCE, label=auto_label)]
    options.extend(
        selector.SelectOptionDict(value=o["value"], label=o["label"])
        for o in adapter_options(hass, address)
    )
    if current not in {o["value"] for o in options}:
        # Previously chosen adapter/proxy is offline: keep it selectable
        missing = "niet beschikbaar" if hass.config.language.startswith("nl") else "unavailable"
        options.append(selector.SelectOptionDict(value=current, label=f"{current} ({missing})"))
    return selector.SelectSelector(
        selector.SelectSelectorConfig(options=options, mode=selector.SelectSelectorMode.DROPDOWN)
    )


def _mode_selector() -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[m.value for m in ReadMode],
            translation_key="mode",
            mode=selector.SelectSelectorMode.LIST,
        )
    )


async def _profile_selector(hass: HomeAssistant) -> selector.SelectSelector:
    registry = await async_get_registry(hass)
    auto_label = (
        "Automatisch (op basis van VIN)" if hass.config.language.startswith("nl") else "Automatic (based on VIN)"
    )
    options = [selector.SelectOptionDict(value=AUTO_PROFILE, label=auto_label)]
    options.extend(
        selector.SelectOptionDict(value=p.id, label=f"{p.name} [{p.status}]")
        for p in registry.profiles
    )
    return selector.SelectSelector(
        selector.SelectSelectorConfig(options=options, mode=selector.SelectSelectorMode.DROPDOWN)
    )


class OBDVehicleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._discovery: bluetooth.BluetoothServiceInfoBleak | None = None
        self._discovered: dict[str, bluetooth.BluetoothServiceInfoBleak] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OBDVehicleOptionsFlow:  # noqa: ANN001
        return OBDVehicleOptionsFlow()

    # ---------------------------------------------------- bluetooth discovery
    async def async_step_bluetooth(
        self, discovery_info: bluetooth.BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info.name or discovery_info.address}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._discovery is not None
        if user_input is not None:
            self._set_ble(self._discovery)
            return await self.async_step_vehicle()
        self._set_confirm_only()
        adapter = identify_adapter(self._discovery.name)
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={
                "name": self._discovery.name or self._discovery.address,
                "model": adapter.model if adapter else "ELM327 BLE",
            },
        )

    # ------------------------------------------------------------ user path
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="user", menu_options=["pick_bluetooth", "tcp", "simulator"]
        )

    async def async_step_pick_bluetooth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            info = self._discovered[user_input[CONF_ADDRESS]]
            await self.async_set_unique_id(info.address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            self._set_ble(info)
            return await self.async_step_vehicle()

        configured = self._async_current_ids(include_ignore=False)
        self._discovered = {}
        for info in bluetooth.async_discovered_service_info(self.hass, connectable=True):
            if info.address in configured or info.address in self._discovered:
                continue
            if not info.name or info.name == info.address:
                continue
            self._discovered[info.address] = info
        if not self._discovered:
            return self.async_abort(reason="no_devices_found")
        ranked = sorted(
            self._discovered.values(), key=lambda i: (not _is_obd_candidate(i), -(i.rssi or -127))
        )
        options = [
            selector.SelectOptionDict(
                value=i.address,
                label=f"{'★ ' if _is_obd_candidate(i) else ''}{i.name} ({i.address}, {i.rssi} dBm)",
            )
            for i in ranked
        ]
        return self.async_show_form(
            step_id="pick_bluetooth",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options)
                    )
                }
            ),
        )

    def _set_ble(self, info: bluetooth.BluetoothServiceInfoBleak) -> None:
        self._data = {
            CONF_CONNECTION: CONN_BLE,
            CONF_ADDRESS: info.address,
            CONF_ADAPTER_NAME: info.name,
        }

    async def async_step_tcp(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            host, port = user_input[CONF_HOST], int(user_input[CONF_PORT])
            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()
            self._data = {CONF_CONNECTION: CONN_TCP, CONF_HOST: host, CONF_PORT: port}
            return await self.async_step_vehicle()
        return self.async_show_form(
            step_id="tcp",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default="192.168.0.10"): str,
                    vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(
                        vol.Coerce(int), vol.Range(min=1, max=65535)
                    ),
                }
            ),
        )

    async def async_step_simulator(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            kind = user_input[CONF_SIM_VEHICLE]
            index = 1 + sum(
                1 for e in self._async_current_entries() if e.data.get(CONF_CONNECTION) == CONN_SIMULATOR
            )
            await self.async_set_unique_id(f"simulator_{kind}_{index}")
            self._data = {CONF_CONNECTION: CONN_SIMULATOR, CONF_SIM_VEHICLE: kind}
            return await self.async_step_vehicle()
        return self.async_show_form(
            step_id="simulator",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SIM_VEHICLE, default="combustion"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=["combustion", "electric"], translation_key="sim_vehicle"
                        )
                    )
                }
            ),
        )

    # ---------------------------------------------------------- vehicle step
    async def async_step_vehicle(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_SKIP_TEST) and self._data[CONF_CONNECTION] != CONN_SIMULATOR:
                errors = await self._async_test_connection(
                    user_input.get(CONF_BT_SOURCE, AUTO_SOURCE)
                )
            if not errors:
                return self.async_create_entry(
                    title=user_input[CONF_VEHICLE_NAME],
                    data={**self._data, CONF_VEHICLE_NAME: user_input[CONF_VEHICLE_NAME]},
                    options={
                        CONF_MODE: user_input[CONF_MODE],
                        CONF_PROFILE: user_input[CONF_PROFILE],
                        CONF_CATEGORIES: DEFAULT_CATEGORIES,
                        CONF_BT_SOURCE: user_input.get(CONF_BT_SOURCE, AUTO_SOURCE),
                    },
                )
        default_name = {
            CONN_BLE: "Auto",
            CONN_TCP: "Auto",
            CONN_SIMULATOR: "Demo EV" if self._data.get(CONF_SIM_VEHICLE) == "electric" else "Demo auto",
        }[self._data[CONF_CONNECTION]]
        schema: dict[Any, Any] = {
            vol.Required(CONF_VEHICLE_NAME, default=default_name): str,
            vol.Required(CONF_PROFILE, default=AUTO_PROFILE): await _profile_selector(self.hass),
            vol.Required(CONF_MODE, default=DEFAULT_MODE): _mode_selector(),
        }
        if self._data[CONF_CONNECTION] == CONN_BLE:
            schema[vol.Required(CONF_BT_SOURCE, default=AUTO_SOURCE)] = _bt_source_selector(
                self.hass, self._data.get(CONF_ADDRESS)
            )
        if self._data[CONF_CONNECTION] != CONN_SIMULATOR:
            schema[vol.Optional(CONF_SKIP_TEST, default=False)] = bool
        return self.async_show_form(
            step_id="vehicle", data_schema=vol.Schema(schema), errors=errors
        )

    async def _async_test_connection(self, source: str = AUTO_SOURCE) -> dict[str, str]:
        connection = self._data[CONF_CONNECTION]
        if connection == CONN_BLE:
            from .core.transport.ble import BleTransport

            address = self._data[CONF_ADDRESS]
            pinned = source != AUTO_SOURCE and pinning_supported()
            if pinned and ble_device_via_source(self.hass, address, source) is None:
                return {"base": "not_seen_by_adapter"}
            transport: Transport = BleTransport(
                (lambda: ble_device_via_source(self.hass, address, source))
                if pinned
                else (lambda: bluetooth.async_ble_device_from_address(self.hass, address, connectable=True)),
                self._data.get(CONF_ADAPTER_NAME) or address,
                client_class=pinned_client_class(source) if pinned else None,
            )
        else:
            from .core.transport.tcp import TcpTransport

            transport = TcpTransport(self._data[CONF_HOST], self._data[CONF_PORT])
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT + 30):
                await _test_adapter(self.hass, transport)
        except AdapterTimeout:
            return {"base": "not_elm327"}
        except (OBDError, TimeoutError) as err:
            _LOGGER.debug("Connection test failed: %s", err)
            return {"base": "cannot_connect"}
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error during connection test")
            return {"base": "unknown"}
        return {}


class OBDVehicleOptionsFlow(OptionsFlow):
    """Options: read mode, intervals, profile, sensor groups, diagnostics."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self.config_entry
        if user_input is not None:
            name = user_input.pop(CONF_VEHICLE_NAME).strip() or entry.title
            if name != entry.data.get(CONF_VEHICLE_NAME):
                self.hass.config_entries.async_update_entry(
                    entry, title=name, data={**entry.data, CONF_VEHICLE_NAME: name}
                )
            return self.async_create_entry(data=user_input)
        opts = entry.options

        def seconds(minimum: int, maximum: int, step: int = 1) -> selector.NumberSelector:
            return selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=minimum,
                    max=maximum,
                    step=step,
                    unit_of_measurement="s",
                    mode=selector.NumberSelectorMode.BOX,
                )
            )

        fields: dict[Any, Any] = {}
        if entry.data.get(CONF_CONNECTION) == CONN_BLE:
            fields[
                vol.Required(CONF_BT_SOURCE, default=opts.get(CONF_BT_SOURCE, AUTO_SOURCE))
            ] = _bt_source_selector(
                self.hass, entry.data.get(CONF_ADDRESS), opts.get(CONF_BT_SOURCE, AUTO_SOURCE)
            )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_VEHICLE_NAME, default=entry.data.get(CONF_VEHICLE_NAME, entry.title)
                ): str,
                **fields,
                vol.Required(CONF_MODE, default=opts.get(CONF_MODE, DEFAULT_MODE)): _mode_selector(),
                vol.Required(
                    CONF_PROFILE, default=opts.get(CONF_PROFILE, AUTO_PROFILE)
                ): await _profile_selector(self.hass),
                vol.Required(
                    CONF_LIVE_INTERVAL, default=opts.get(CONF_LIVE_INTERVAL, DEFAULT_LIVE_INTERVAL)
                ): seconds(5, 600),
                vol.Required(
                    CONF_CHARGING_INTERVAL,
                    default=opts.get(CONF_CHARGING_INTERVAL, DEFAULT_CHARGING_INTERVAL),
                ): seconds(30, 3600),
                vol.Required(
                    CONF_PARKED_INTERVAL,
                    default=opts.get(CONF_PARKED_INTERVAL, DEFAULT_PARKED_INTERVAL),
                ): seconds(60, 86400),
                vol.Required(
                    CONF_PROBE_INTERVAL,
                    default=opts.get(CONF_PROBE_INTERVAL, DEFAULT_PROBE_INTERVAL),
                ): seconds(0, 86400),
                vol.Required(
                    CONF_FULL_INTERVAL, default=opts.get(CONF_FULL_INTERVAL, DEFAULT_FULL_INTERVAL)
                ): seconds(60, 86400),
                vol.Required(
                    CONF_CATEGORIES, default=opts.get(CONF_CATEGORIES, DEFAULT_CATEGORIES)
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=ALL_CATEGORIES,
                        multiple=True,
                        translation_key="categories",
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
                vol.Required(CONF_ADVANCED, default=opts.get(CONF_ADVANCED, False)): bool,
                vol.Required(CONF_LOG_TRAFFIC, default=opts.get(CONF_LOG_TRAFFIC, False)): bool,
                vol.Required(
                    CONF_COMMAND_TIMEOUT,
                    default=opts.get(CONF_COMMAND_TIMEOUT, DEFAULT_COMMAND_TIMEOUT),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=30, step=0.5, unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
