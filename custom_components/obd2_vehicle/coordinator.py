"""Session manager / coordinator: one instance per adapter (= per vehicle).

Every config entry owns exactly one coordinator, one transport, one ELM327
driver (with its own command lock) and one persisted identity. Several
adapters / vehicles therefore run fully independently and concurrently.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .bluetooth_util import (
    AUTO_SOURCE,
    ble_device_via_source,
    pinned_client_class,
    pinning_supported,
    rssi_via_source,
)
from .const import (
    ARRIVAL_MIN_ABSENCE,
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
    EVENT_DTC_DETECTED,
    EVENT_READ_COMPLETED,
    SESSION_TIMEOUT,
    STATE_CONNECTED,
    STATE_CONNECTING,
    STATE_ERROR,
    STATE_IDLE,
    STATE_NOT_IN_RANGE,
    STATE_READING,
    STATE_VEHICLE_OFF,
    STORAGE_VERSION,
)
from .core.adapters import identify_adapter
from .core.elm327 import ELM327
from .core.exceptions import (
    AdapterError,
    AdapterTimeout,
    NoDataError,
    OBDError,
    TransportError,
    TransportUnavailable,
    VehicleUnreachable,
)
from .core.pids import all_value_specs
from .core.profiles import ProfileRegistry
from .core.reader import AUTO_PROFILE, DtcReport, VehicleIdentity, VehicleReader
from .core.scheduler import Plan, PlannerConfig, PlannerState, ReadMode, plan
from .core.simulator import (
    ElmSimulator,
    SimulatedTransport,
    demo_combustion_vehicle,
    demo_electric_vehicle,
)
from .core.specs import ValueSpec
from .core.transport.base import Transport
from .core.vin import decode_vin, vin_fingerprint

_LOGGER = logging.getLogger(__name__)

type OBDConfigEntry = ConfigEntry[OBDVehicleCoordinator]


@dataclass(slots=True)
class VehicleData:
    """Everything the entities need. ``values`` are last known values."""

    connection_state: str = STATE_IDLE
    adapter_present: bool = False
    connected: bool = False
    vehicle_responding: bool | None = None
    session_active: bool = False
    rssi: int | None = None
    values: dict[str, Any] = field(default_factory=dict)
    value_times: dict[str, datetime] = field(default_factory=dict)
    live_keys: set[str] = field(default_factory=set)
    adapter_voltage: float | None = None
    adapter_voltage_time: datetime | None = None
    dtcs: DtcReport | None = None
    last_connected: datetime | None = None
    last_read: datetime | None = None
    last_full_read: datetime | None = None
    last_attempt: datetime | None = None
    last_error: str | None = None
    adapter_identity: str | None = None
    protocol: str | None = None


class OBDVehicleCoordinator(DataUpdateCoordinator[VehicleData]):
    """Manages connection, scheduling and reading for one adapter."""

    config_entry: OBDConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: OBDConfigEntry, registry: ProfileRegistry
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{entry.data.get(CONF_VEHICLE_NAME, entry.title)}",
            update_interval=None,
        )
        self.registry = registry
        self.data = VehicleData()
        opts = entry.options
        self.connection: str = entry.data[CONF_CONNECTION]
        self.address: str | None = entry.data.get(CONF_ADDRESS)
        self.vehicle_name: str = entry.data.get(CONF_VEHICLE_NAME, entry.title)
        self.planner_cfg = PlannerConfig(
            mode=ReadMode(opts.get(CONF_MODE, DEFAULT_MODE)),
            live_interval=float(opts.get(CONF_LIVE_INTERVAL, DEFAULT_LIVE_INTERVAL)),
            charging_interval=float(opts.get(CONF_CHARGING_INTERVAL, DEFAULT_CHARGING_INTERVAL)),
            parked_interval=float(opts.get(CONF_PARKED_INTERVAL, DEFAULT_PARKED_INTERVAL)),
            probe_interval=float(opts.get(CONF_PROBE_INTERVAL, DEFAULT_PROBE_INTERVAL)),
            full_read_interval=float(opts.get(CONF_FULL_INTERVAL, DEFAULT_FULL_INTERVAL)),
        )
        self.planner = PlannerState()
        self.profile_setting: str = opts.get(CONF_PROFILE, AUTO_PROFILE)
        self.categories: list[str] = list(opts.get(CONF_CATEGORIES, DEFAULT_CATEGORIES))
        self.advanced: bool = bool(opts.get(CONF_ADVANCED, False))
        self.log_traffic: bool = bool(opts.get(CONF_LOG_TRAFFIC, False))
        self.command_timeout = float(opts.get(CONF_COMMAND_TIMEOUT, DEFAULT_COMMAND_TIMEOUT))
        #: Bluetooth adapter / proxy source to connect through ("auto" = best path)
        self.bluetooth_source: str = opts.get(CONF_BT_SOURCE, AUTO_SOURCE)

        self.identity = VehicleIdentity()
        self.preferred_layout: str | None = None
        self.known_keys: set[str] = set()
        self.last_plan: Plan | None = None
        self.unavailable: dict[str, str] = {}

        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self._transport: Transport | None = None
        self._elm: ELM327 | None = None
        self._reader: VehicleReader | None = None
        self._vehicle_started = False
        self._wake = asyncio.Event()
        self._runner: asyncio.Task[None] | None = None
        self._unsubs: list[CALLBACK_TYPE] = []
        self._absent_since: float | None = None
        self._last_disconnect: float = 0.0
        self._new_spec_listeners: list[Callable[[list[ValueSpec]], None]] = []
        self._simulator: ElmSimulator | None = None
        self._session_lock = asyncio.Lock()

    # ================================================================ setup
    async def async_setup(self) -> None:
        """Load persisted state and start presence tracking + the runner."""
        stored = await self._store.async_load() or {}
        self.identity = VehicleIdentity.from_dict(stored.get("identity"))
        self.preferred_layout = stored.get("preferred_layout")
        self.known_keys = set(stored.get("known_keys", []))
        for key, (value, ts) in stored.get("values", {}).items():
            self.data.values[key] = value
            if parsed := dt_util.parse_datetime(ts):
                self.data.value_times[key] = parsed
        for attr in ("last_read", "last_connected", "last_full_read"):
            if stored.get(attr):
                setattr(self.data, attr, dt_util.parse_datetime(stored[attr]))
        if stored.get("dtcs"):
            self.data.dtcs = DtcReport(**stored["dtcs"])
        if stored.get("adapter_voltage") is not None:
            self.data.adapter_voltage = stored["adapter_voltage"]
        self.data.adapter_identity = stored.get("adapter_identity")
        self.data.protocol = stored.get("protocol")

        now = time.monotonic()
        if self.connection == CONN_BLE:
            assert self.address is not None
            self._unsubs.append(
                bluetooth.async_register_callback(
                    self.hass,
                    self._async_on_advertisement,
                    bluetooth.BluetoothCallbackMatcher(address=self.address, connectable=True),
                    bluetooth.BluetoothScanningMode.ACTIVE,
                )
            )
            self._unsubs.append(
                bluetooth.async_track_unavailable(
                    self.hass, self._async_on_unavailable, self.address, connectable=True
                )
            )
            present = bluetooth.async_address_present(self.hass, self.address, connectable=True)
            if service_info := bluetooth.async_last_service_info(
                self.hass, self.address, connectable=True
            ):
                self.data.rssi = self._rssi(service_info.rssi)
        else:
            present = True
        self._set_present(present, now, arrival=present)

    @callback
    def async_start(self) -> None:
        """Start the read loop (after the entity platforms are set up)."""
        self._runner = self.config_entry.async_create_background_task(
            self.hass, self._run(), f"{DOMAIN} runner {self.vehicle_name}"
        )

    async def async_shutdown(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._runner is not None:
            self._runner.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._runner
        await self._disconnect()
        await self._async_save(immediate=True)
        await super().async_shutdown()

    async def _async_update_data(self) -> VehicleData:
        # Polling is driven by the runner; refresh requests just return state.
        return self.data

    # ============================================================ presence
    @callback
    def _async_on_advertisement(
        self, service_info: bluetooth.BluetoothServiceInfoBleak, change: bluetooth.BluetoothChange
    ) -> None:
        self.data.rssi = self._rssi(service_info.rssi)
        now = time.monotonic()
        if not self.planner.present:
            arrival = self._absent_since is None or now - self._absent_since >= ARRIVAL_MIN_ABSENCE
            _LOGGER.debug(
                "%s: adapter %s visible again (arrival=%s)", self.vehicle_name, self.address, arrival
            )
            self._set_present(True, now, arrival=arrival)
            self.async_update_listeners()

    @callback
    def _async_on_unavailable(self, _service_info: bluetooth.BluetoothServiceInfoBleak) -> None:
        # A connected BLE peripheral stops advertising – that is not "gone".
        if self.data.connected:
            return
        _LOGGER.debug("%s: adapter %s out of range / asleep", self.vehicle_name, self.address)
        self._absent_since = time.monotonic()
        self._set_present(False, self._absent_since, arrival=False)
        self.async_update_listeners()

    def _rssi(self, fallback: int | None) -> int | None:
        if self.bluetooth_source == AUTO_SOURCE or self.address is None:
            return fallback
        return rssi_via_source(self.hass, self.address, self.bluetooth_source)

    def _set_present(self, present: bool, now: float, *, arrival: bool) -> None:
        self.planner.present = present
        self.data.adapter_present = present
        if present and arrival:
            self.planner.arrived_at = now
            self.planner.snapshot_done_for_arrival = False
            self.planner.arrival_attempts = 0
            self.planner.ecu_awake = None
            self.planner.connect_failures = 0
        if not present:
            self.data.connection_state = STATE_NOT_IN_RANGE
            self.data.rssi = None
        elif self.data.connection_state == STATE_NOT_IN_RANGE:
            self.data.connection_state = STATE_IDLE
        self._wake.set()

    # ============================================================== public
    @callback
    def async_request_read(self, *, full: bool = True) -> None:
        """Manual read (button)."""
        self.planner.manual_request = True
        self.planner.manual_full = full
        self._wake.set()

    async def async_reconnect(self) -> None:
        """Drop the link and start over (button)."""
        await self._disconnect()
        self.planner.connect_failures = 0
        self.async_request_read(full=False)

    async def async_reidentify(self) -> None:
        """Forget cached capabilities and identify the vehicle again (button)."""
        self.identity = VehicleIdentity()
        self._vehicle_started = False
        await self._async_save(immediate=True)
        self.async_request_read(full=True)

    @callback
    def async_add_spec_listener(self, listener: Callable[[list[ValueSpec]], None]) -> CALLBACK_TYPE:
        self._new_spec_listeners.append(listener)

        def remove() -> None:
            self._new_spec_listeners.remove(listener)

        return remove

    def known_specs(self) -> list[ValueSpec]:
        """Specs for every key that has ever been seen for this vehicle."""
        catalog = {s.key: s for s in all_value_specs()}
        profile_id = (
            self.identity.profile_id if self.profile_setting == AUTO_PROFILE else self.profile_setting
        )
        catalog.update(self.registry.get(profile_id).value_specs)
        return [catalog[k] for k in sorted(self.known_keys) if k in catalog]

    # =============================================================== runner
    async def _run(self) -> None:
        while True:
            self._wake.clear()
            next_plan = plan(self.planner_cfg, self.planner, time.monotonic())
            self.last_plan = next_plan
            if next_plan.run_in is None or next_plan.run_in > 0:
                if self.data.connected and not (next_plan.run_in and next_plan.keep_connected):
                    await self._disconnect()
                    self.async_update_listeners()
                _LOGGER.debug(
                    "%s: next read in %s s (%s)", self.vehicle_name, next_plan.run_in, next_plan.reason
                )
                with contextlib.suppress(TimeoutError):
                    async with asyncio.timeout(next_plan.run_in):
                        await self._wake.wait()
                continue
            await self._session(next_plan)

    def _build_transport(self) -> Transport:
        data = self.config_entry.data
        if self.connection == CONN_BLE:
            from .core.transport.ble import BleTransport  # bleak only needed here

            address = self.address
            assert address is not None
            source = self.bluetooth_source
            client_class = None
            if source != AUTO_SOURCE:
                if pinning_supported():
                    client_class = pinned_client_class(source)
                else:
                    _LOGGER.warning(
                        "%s: this Home Assistant version does not allow choosing the "
                        "Bluetooth adapter; using automatic selection",
                        self.vehicle_name,
                    )
                    source = AUTO_SOURCE

            def provider():  # noqa: ANN202 - BLEDevice | None
                if source != AUTO_SOURCE:
                    return ble_device_via_source(self.hass, address, source)
                return bluetooth.async_ble_device_from_address(self.hass, address, connectable=True)

            if source != AUTO_SOURCE and provider() is None:
                raise TransportUnavailable(
                    f"{self.vehicle_name} is not seen by the selected Bluetooth adapter/proxy {source}"
                )
            return BleTransport(
                provider,
                self.vehicle_name,
                preferred_layout=self.preferred_layout,
                client_class=client_class,
            )
        if self.connection == CONN_TCP:
            from .core.transport.tcp import TcpTransport

            return TcpTransport(data[CONF_HOST], int(data.get(CONF_PORT, DEFAULT_PORT)))
        if self._simulator is None:
            vehicle = (
                demo_electric_vehicle()
                if data.get(CONF_SIM_VEHICLE) == "electric"
                else demo_combustion_vehicle()
            )
            self._simulator = ElmSimulator(vehicle)
        return SimulatedTransport(self._simulator, latency=0.02)

    async def _ensure_connected(self) -> None:
        if self._transport is not None and self._transport.is_connected and self._elm:
            return
        await self._disconnect()
        self.data.connection_state = STATE_CONNECTING
        self.async_update_listeners()
        transport = self._build_transport()
        transport.set_disconnect_callback(self._on_link_lost)
        elm = ELM327(transport, command_timeout=self.command_timeout, log_traffic=self.log_traffic)
        _LOGGER.debug("%s: connecting via %s", self.vehicle_name, transport.kind)
        await transport.connect(CONNECT_TIMEOUT)
        self._transport, self._elm = transport, elm
        self.data.connected = True
        self.data.last_connected = dt_util.utcnow()
        while True:
            try:
                info = await elm.initialize()
                break
            except AdapterTimeout:
                next_layout = getattr(transport, "next_layout", None)
                if next_layout is not None and await next_layout():
                    _LOGGER.debug("%s: no answer, trying next GATT layout", self.vehicle_name)
                    continue
                raise
        layout = getattr(transport, "active_layout", None)
        if layout and layout != self.preferred_layout:
            self.preferred_layout = layout
        self.data.adapter_identity = info.identity
        _LOGGER.debug("%s: adapter initialised (%s)", self.vehicle_name, info.identity)
        self._reader = VehicleReader(
            elm,
            self.registry,
            self.identity,
            profile_setting=self.profile_setting,
            enabled_categories=self.categories,
        )
        self._vehicle_started = False

    @callback
    def _on_link_lost(self) -> None:
        _LOGGER.debug("%s: link lost", self.vehicle_name)
        self.data.connected = False
        self.data.session_active = False
        self._vehicle_started = False
        self._last_disconnect = time.monotonic()
        self.hass.loop.call_soon_threadsafe(self._wake.set)
        self.hass.loop.call_soon_threadsafe(self.async_update_listeners)

    async def _disconnect(self) -> None:
        transport, self._transport = self._transport, None
        self._elm = None
        self._reader = None
        self._vehicle_started = False
        if transport is not None:
            await transport.disconnect()
            self._last_disconnect = time.monotonic()
        self.data.connected = False
        self.data.session_active = False
        self.data.live_keys = set()
        if self.data.connection_state in (STATE_CONNECTED, STATE_READING, STATE_CONNECTING):
            self.data.connection_state = (
                STATE_IDLE if self.planner.present else STATE_NOT_IN_RANGE
            )

    # ============================================================== session
    async def _session(self, current: Plan) -> None:
        async with self._session_lock:
            manual = self.planner.manual_request
            self.planner.manual_request = False
            self.planner.last_attempt = time.monotonic()
            self.data.last_attempt = dt_util.utcnow()
            full = current.full or manual and self.planner.manual_full
            try:
                async with asyncio.timeout(SESSION_TIMEOUT):
                    await self._read(full)
            except (VehicleUnreachable, NoDataError) as err:
                _LOGGER.debug("%s: vehicle does not answer: %s", self.vehicle_name, err)
                self.planner.ecu_awake = False
                self.planner.engine_running = False
                if not self.planner.snapshot_done_for_arrival:
                    self.planner.arrival_attempts += 1
                self.data.vehicle_responding = False
                self.data.connection_state = STATE_VEHICLE_OFF
                self.data.last_error = None
                await self._disconnect()
                self.data.connection_state = STATE_VEHICLE_OFF
            except (TransportUnavailable, TransportError, AdapterTimeout, AdapterError, TimeoutError) as err:
                self.planner.connect_failures += 1
                msg = str(err) or type(err).__name__
                level = logging.INFO if self.planner.connect_failures == 1 else logging.DEBUG
                _LOGGER.log(level, "%s: connection problem: %s", self.vehicle_name, msg)
                self.data.last_error = msg
                await self._disconnect()
                self.data.connection_state = STATE_ERROR if self.planner.present else STATE_NOT_IN_RANGE
                if self.connection != CONN_BLE and self.planner.connect_failures >= 3:
                    self.data.adapter_present = False
            except OBDError as err:
                self.planner.connect_failures += 1
                _LOGGER.warning("%s: unexpected OBD error: %s", self.vehicle_name, err)
                self.data.last_error = str(err)
                await self._disconnect()
                self.data.connection_state = STATE_ERROR
            except Exception:  # noqa: BLE001 - never kill the runner
                self.planner.connect_failures += 1
                _LOGGER.exception("%s: unexpected error during read session", self.vehicle_name)
                self.data.last_error = "internal error (see log)"
                await self._disconnect()
                self.data.connection_state = STATE_ERROR
            finally:
                if self._reader is not None:
                    self.unavailable = dict(self._reader.unavailable)
                await self._async_save()
                self.async_set_updated_data(self.data)

    async def _read(self, full: bool) -> None:
        await self._ensure_connected()
        assert self._reader is not None and self._elm is not None
        self.planner.connect_failures = 0
        self.data.adapter_present = True
        self.data.connection_state = STATE_READING
        self.async_update_listeners()

        with contextlib.suppress(OBDError):
            voltage = await self._elm.read_adapter_voltage()
            if voltage is not None:
                self.data.adapter_voltage = voltage
                self.data.adapter_voltage_time = dt_util.utcnow()

        if not self._vehicle_started:
            await self._reader.start()
            self._vehicle_started = True
            full = True
            self.identity = self._reader.identity
            self.data.protocol = self._elm.info.protocol_name
            if self._reader.vehicle_changed:
                self.known_keys.clear()
            self._update_device()

        values = await self._reader.read_values(full=full)
        now = dt_util.utcnow()
        self.planner.ecu_awake = True
        self.data.vehicle_responding = True
        self.data.session_active = True
        self.data.live_keys = set(values)
        self.data.values.update(values)
        for key in values:
            self.data.value_times[key] = now
        self.data.last_read = now
        self.planner.last_success = time.monotonic()
        self.planner.engine_running = bool(values.get("rpm"))
        charging = values.get("charging")
        if charging is None and values.get("charging_power") is not None:
            charging = values["charging_power"] > 0.3
        self.planner.charging = bool(charging)

        if full:
            dtcs = await self._reader.read_dtcs(
                permanent=self.advanced, freeze_frame=self.advanced
            )
            self._handle_dtcs(dtcs)
            self.planner.last_full = time.monotonic()
            self.data.last_full_read = now
        self.planner.snapshot_done_for_arrival = True
        self.planner.arrival_attempts = 0
        self.data.connection_state = STATE_CONNECTED
        self.data.last_error = None
        self._register_new_keys()
        self.hass.bus.async_fire(
            EVENT_READ_COMPLETED,
            {"entry_id": self.config_entry.entry_id, "vehicle": self.vehicle_name, "full": full},
        )

    def _handle_dtcs(self, dtcs: DtcReport) -> None:
        previous = set(self.data.dtcs.stored) if self.data.dtcs else set()
        if self.data.dtcs and dtcs.permanent is None:
            dtcs.permanent = self.data.dtcs.permanent
        self.data.dtcs = dtcs
        new = [code for code in dtcs.stored if code not in previous]
        if new:
            self.hass.bus.async_fire(
                EVENT_DTC_DETECTED,
                {"entry_id": self.config_entry.entry_id, "vehicle": self.vehicle_name, "codes": new},
            )

    def _register_new_keys(self) -> None:
        assert self._reader is not None
        specs = self._reader.value_specs()
        new = [spec for key, spec in specs.items() if key not in self.known_keys]
        if not new:
            return
        self.known_keys.update(spec.key for spec in new)
        _LOGGER.debug("%s: new values available: %s", self.vehicle_name, [s.key for s in new])
        for listener in list(self._new_spec_listeners):
            listener(new)

    def _update_device(self) -> None:
        """Set manufacturer/model on the vehicle device once it is identified."""
        info = decode_vin(self.identity.vin) if self.identity.vin else None
        profile = self.registry.get(self.identity.profile_id)
        adapter = (
            identify_adapter(self.config_entry.data.get(CONF_ADAPTER_NAME))
            if self.connection == CONN_BLE
            else None
        )
        manufacturer = (info.manufacturer if info else None) or (
            profile.manufacturers[0] if profile.manufacturers else None
        )
        kwargs: dict[str, Any] = {"model": profile.name}
        if manufacturer:
            kwargs["manufacturer"] = manufacturer
        if adapter:
            kwargs["hw_version"] = f"{adapter.manufacturer} {adapter.model}"
        if self.data.adapter_identity:
            kwargs["sw_version"] = self.data.adapter_identity
        dr.async_get(self.hass).async_get_or_create(
            config_entry_id=self.config_entry.entry_id,
            identifiers={(DOMAIN, self.config_entry.entry_id)},
            **kwargs,
        )

    # =============================================================== store
    async def _async_save(self, *, immediate: bool = False) -> None:
        def data() -> dict[str, Any]:
            return {
                "identity": self.identity.as_dict(),
                "preferred_layout": self.preferred_layout,
                "known_keys": sorted(self.known_keys),
                "values": {
                    k: (v, self.data.value_times[k].isoformat())
                    for k, v in self.data.values.items()
                    if k in self.data.value_times
                },
                "last_read": self.data.last_read.isoformat() if self.data.last_read else None,
                "last_connected": (
                    self.data.last_connected.isoformat() if self.data.last_connected else None
                ),
                "last_full_read": (
                    self.data.last_full_read.isoformat() if self.data.last_full_read else None
                ),
                "dtcs": (
                    {
                        "stored": self.data.dtcs.stored,
                        "pending": self.data.dtcs.pending,
                        "permanent": self.data.dtcs.permanent,
                        "freeze_frame_dtc": self.data.dtcs.freeze_frame_dtc,
                    }
                    if self.data.dtcs
                    else None
                ),
                "adapter_voltage": self.data.adapter_voltage,
                "adapter_identity": self.data.adapter_identity,
                "protocol": self.data.protocol,
            }

        if immediate:
            await self._store.async_save(data())
        else:
            self._store.async_delay_save(data, 15)

    async def async_remove_store(self) -> None:
        await self._store.async_remove()

    # ========================================================= diagnostics
    def diagnostics(self) -> dict[str, Any]:
        identity = self.identity.as_dict()
        vin = identity.pop("vin", None)
        info = decode_vin(vin) if vin else None
        return {
            "vehicle": {
                "vin_present": bool(vin),
                "vin_fingerprint": vin_fingerprint(vin) if vin else None,
                "manufacturer": info.manufacturer if info else None,
                "model_year": info.model_year if info else None,
                **identity,
            },
            "planner": {
                "mode": self.planner_cfg.mode,
                "state": {
                    "present": self.planner.present,
                    "ecu_awake": self.planner.ecu_awake,
                    "engine_running": self.planner.engine_running,
                    "charging": self.planner.charging,
                    "connect_failures": self.planner.connect_failures,
                    "snapshot_done_for_arrival": self.planner.snapshot_done_for_arrival,
                },
                "next": (
                    {"run_in": self.last_plan.run_in, "reason": self.last_plan.reason}
                    if self.last_plan
                    else None
                ),
            },
            "state": {
                "connection_state": self.data.connection_state,
                "connected": self.data.connected,
                "vehicle_responding": self.data.vehicle_responding,
                "rssi": self.data.rssi,
                "last_read": self.data.last_read,
                "last_full_read": self.data.last_full_read,
                "last_connected": self.data.last_connected,
                "last_attempt": self.data.last_attempt,
                "last_error": self.data.last_error,
                "protocol": self.data.protocol,
                "adapter": self.data.adapter_identity,
                "preferred_gatt_layout": self.preferred_layout,
                "bluetooth_source": (
                    "auto" if self.bluetooth_source == AUTO_SOURCE else "selected adapter/proxy"
                ),
            },
            "values": {k: v for k, v in self.data.values.items()},
            "unavailable_reasons": self.unavailable,
            "known_keys": sorted(self.known_keys),
            "profile_errors": self.registry.errors,
            "adapter": self._elm.diagnostics() if self._elm else None,
            "transport": self._transport.diagnostics() if self._transport else None,
        }
