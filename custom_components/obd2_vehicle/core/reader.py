"""Vehicle level read logic on top of an initialised :class:`ELM327`.

The reader is responsible for:
* protocol detection and vehicle identification (VIN)
* discovering supported Mode 01 PIDs (bitmaps) per ECU
* selecting / auto-detecting the vehicle profile
* reading values (batched multi-PID requests on CAN), DTCs and freeze frame
* recording why values are not available
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .dtc import decode_dtc_payload, format_dtc
from .elm327 import ELM327
from .exceptions import (
    AdapterError,
    AdapterTimeout,
    NoDataError,
    ResponseParseError,
    VehicleUnreachable,
)
from .expression import to_wican_bytes
from .pids import DERIVED_VALUES, PIDS, SUPPORT_PIDS, decode_support_bitmap
from .profiles import GENERIC_PROFILE_ID, ProfileRegistry, ProfileRequest, VehicleProfile
from .protocol import BusKind, Message
from .specs import CAT_DIAGNOSTIC, ValueSpec
from .vin import parse_vin_payload

_LOGGER = logging.getLogger(__name__)

AUTO_PROFILE = "auto"

#: Mode 01 PIDs read on every (fast) cycle in addition to realtime PIDs.
_FAST_STATIC_PIDS = {0x01, 0x2F, 0x5B}

#: Maximum PIDs per Mode 01 request (ISO 15031-5 allows 6 on CAN).
_MAX_BATCH = 6

#: Errors that mean "this request failed" but the vehicle is still talking.
_REQUEST_ERRORS = (NoDataError, ResponseParseError, AdapterError)


@dataclass(slots=True)
class VehicleIdentity:
    """Identification and capability cache, persisted between sessions."""

    vin: str | None = None
    protocol: str | None = None
    supported_pids: dict[int, str] = field(default_factory=dict)  # pid -> ecu id
    multi_pid: bool | None = None
    profile_id: str | None = None
    failed_profile_requests: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "vin": self.vin,
            "protocol": self.protocol,
            "supported_pids": {f"{k:02X}": v for k, v in self.supported_pids.items()},
            "multi_pid": self.multi_pid,
            "profile_id": self.profile_id,
            "failed_profile_requests": list(self.failed_profile_requests),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> VehicleIdentity:
        if not data:
            return cls()
        return cls(
            vin=data.get("vin"),
            protocol=data.get("protocol"),
            supported_pids={int(k, 16): v for k, v in data.get("supported_pids", {}).items()},
            multi_pid=data.get("multi_pid"),
            profile_id=data.get("profile_id"),
            failed_profile_requests=list(data.get("failed_profile_requests", [])),
        )


@dataclass(slots=True)
class DtcReport:
    stored: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    permanent: list[str] | None = None
    freeze_frame_dtc: str | None = None


class VehicleReader:
    """Reads a vehicle through an ELM327."""

    def __init__(
        self,
        elm: ELM327,
        registry: ProfileRegistry,
        identity: VehicleIdentity | None = None,
        *,
        profile_setting: str = AUTO_PROFILE,
        enabled_categories: Iterable[str] | None = None,
    ) -> None:
        self.elm = elm
        self.registry = registry
        self.identity = identity or VehicleIdentity()
        self.profile_setting = profile_setting
        self.enabled_categories = set(enabled_categories) if enabled_categories else None
        self.profile: VehicleProfile = registry.get(GENERIC_PROFILE_ID)
        self.unavailable: dict[str, str] = {}
        self.vehicle_changed = False
        self._profile_failures: dict[str, int] = {}

    # ---------------------------------------------------------------- setup
    async def start(self) -> None:
        """Detect protocol, identify vehicle, discover PIDs and select profile.

        Raises VehicleUnreachable when no ECU answers (ignition off).
        """
        info = await self.elm.detect_protocol()
        vin = await self.read_vin()
        self.vehicle_changed = bool(vin and self.identity.vin and vin != self.identity.vin)
        if self.vehicle_changed:
            _LOGGER.warning(
                "A different vehicle is connected to this adapter; capabilities are re-detected"
            )
            self.identity = VehicleIdentity()
        if vin:
            self.identity.vin = vin
        if self.identity.protocol != info.protocol_number:
            # Protocol changed (or first run) -> capabilities must be rediscovered
            self.identity.supported_pids = {}
            self.identity.multi_pid = None
        self.identity.protocol = info.protocol_number
        if not self.identity.supported_pids:
            await self.discover_pids()
        await self.select_profile()

    async def read_vin(self) -> str | None:
        try:
            messages = await self.elm.query("0902", timeout=6.0)
        except _REQUEST_ERRORS as err:
            _LOGGER.debug("VIN not available: %s", err)
            return None
        for message in self._sorted(messages):
            vin = parse_vin_payload(message.data)
            if vin:
                return vin
        _LOGGER.debug("VIN response could not be decoded")
        return None

    async def discover_pids(self) -> dict[int, str]:
        """Read 'PIDs supported' bitmaps of every ECU."""
        supported: dict[int, str] = {}
        ecu_support: dict[str, set[int]] = {}
        base_pids = [0x00]
        while base_pids:
            base = base_pids.pop(0)
            try:
                messages = await self.elm.query(f"01{base:02X}")
            except _REQUEST_ERRORS as err:
                _LOGGER.debug("Support bitmap 01%02X not available: %s", base, err)
                continue
            next_needed = False
            for message in messages:
                if len(message.data) < 6 or message.data[1] != base:
                    continue
                pids = decode_support_bitmap(base, message.data[2:6])
                ecu_support.setdefault(message.ecu, set()).update(pids)
                if base + 0x20 in pids:
                    next_needed = True
            if next_needed and base + 0x20 in SUPPORT_PIDS:
                base_pids.append(base + 0x20)
        # Prefer the engine / main ECU (lowest response id) for each PID
        for ecu in sorted(ecu_support):
            for pid in ecu_support[ecu]:
                if pid not in SUPPORT_PIDS:
                    supported.setdefault(pid, ecu)
        self.identity.supported_pids = supported
        _LOGGER.info(
            "Supported Mode 01 PIDs: %s",
            ", ".join(f"{pid:02X}" for pid in sorted(supported)) or "none",
        )
        return supported

    async def select_profile(self) -> VehicleProfile:
        setting = self.profile_setting
        if setting != AUTO_PROFILE:
            self.profile = self.registry.get(setting)
        elif self.identity.profile_id and self.identity.profile_id in self.registry:
            self.profile = self.registry.get(self.identity.profile_id)
        else:
            self.profile = self.registry.get(GENERIC_PROFILE_ID)
            for candidate in self.registry.candidates_for_vin(self.identity.vin):
                if await self._probe_profile(candidate):
                    self.profile = candidate
                    break
        self.identity.profile_id = self.profile.id
        _LOGGER.info("Vehicle profile: %s", self.profile.name)
        return self.profile

    async def _probe_profile(self, profile: VehicleProfile) -> bool:
        request = profile.request_by_id(profile.probe or "")
        if request is None:
            return False
        try:
            values = await self._run_profile_request(profile, request)
        except _REQUEST_ERRORS as err:
            _LOGGER.debug("Profile %s does not match: %s", profile.id, err)
            return False
        finally:
            await self._safe_reset_addressing()
        return bool(values)

    # --------------------------------------------------------------- reading
    def _category_enabled(self, spec: ValueSpec) -> bool:
        return self.enabled_categories is None or spec.category in self.enabled_categories

    def _pids_to_read(self, full: bool) -> list[int]:
        pids: list[int] = []
        for pid, ecu in sorted(self.identity.supported_pids.items()):
            definition = PIDS.get(pid)
            if definition is None:
                continue
            if not any(self._category_enabled(s) for s in definition.values) and pid != 0x01:
                continue
            realtime = any(s.realtime for s in definition.values)
            if full or realtime or pid in _FAST_STATIC_PIDS:
                pids.append(pid)
        return pids

    async def read_values(self, *, full: bool) -> dict[str, Any]:
        """Read Mode 01 values and profile values.

        Raises VehicleUnreachable when the ECU stopped answering completely.
        """
        values: dict[str, Any] = {}
        pids = self._pids_to_read(full)
        await self._read_mode01(pids, values)
        await self._read_profile(values, full=full)
        self._derive(values)
        return values

    async def _read_mode01(self, pids: list[int], values: dict[str, Any]) -> None:
        if not pids:
            return
        can = self.elm.bus in (BusKind.CAN_11, BusKind.CAN_29)
        batches: list[list[int]]
        if can and self.identity.multi_pid is not False:
            batches = [pids[i : i + _MAX_BATCH] for i in range(0, len(pids), _MAX_BATCH)]
        else:
            batches = [[pid] for pid in pids]
        answered_any = False
        failures = 0
        for batch in batches:
            request = "01" + "".join(f"{pid:02X}" for pid in batch)
            try:
                messages = await self.elm.query(request)
            except NoDataError as err:
                if len(batch) == 1:
                    failures += 1
                    self._mark_unavailable(batch[0], f"no answer ({err})")
                    continue
                # Some ECUs reject multi-PID requests: fall back to single PIDs
                self.identity.multi_pid = False
                messages = []
            except (ResponseParseError, AdapterError) as err:
                failures += 1
                for pid in batch:
                    self._mark_unavailable(pid, f"invalid answer ({err})")
                continue
            answered_any = answered_any or bool(messages)
            decoded = self._decode_mode01(messages, batch)
            missing = [pid for pid in batch if pid not in decoded]
            if len(batch) > 1:
                if missing and len(batch) - len(missing) <= 1:
                    # Only the first PID answered: ECU ignores multi-PID requests
                    self.identity.multi_pid = False
                elif not missing:
                    self.identity.multi_pid = True
            for pid in missing:
                if len(batch) > 1:
                    # Retry individually (ECU may not support multi-PID requests)
                    try:
                        single = await self.elm.query(f"01{pid:02X}")
                        decoded.update(self._decode_mode01(single, [pid]))
                        answered_any = True
                    except _REQUEST_ERRORS as err:
                        failures += 1
                        self._mark_unavailable(pid, f"no answer ({err})")
                        continue
                if pid not in decoded:
                    self._mark_unavailable(pid, "ECU did not return this PID")
            for pid, result in decoded.items():
                for key, value in result.items():
                    values[key] = value
                    self.unavailable.pop(key, None)
        if not answered_any and failures:
            raise VehicleUnreachable("ECU did not answer any Mode 01 request")

    def _mark_unavailable(self, pid: int, reason: str) -> None:
        definition = PIDS.get(pid)
        if definition:
            for spec in definition.values:
                self.unavailable[spec.key] = reason

    def _decode_mode01(self, messages: list[Message], batch: list[int]) -> dict[int, dict[str, Any]]:
        result: dict[int, dict[str, Any]] = {}
        for message in self._sorted(messages):
            data = message.data
            idx = 1
            while idx < len(data):
                pid = data[idx]
                definition = PIDS.get(pid)
                if definition is None or pid not in batch:
                    break
                chunk = data[idx + 1 : idx + 1 + definition.length]
                if len(chunk) < definition.length:
                    break
                preferred = self.identity.supported_pids.get(pid)
                if pid not in result or preferred == message.ecu:
                    try:
                        result[pid] = definition.decode(chunk)
                    except (IndexError, ValueError) as err:
                        _LOGGER.debug("Decoding PID %02X failed: %s", pid, err)
                idx += 1 + definition.length
        return result

    async def _read_profile(self, values: dict[str, Any], *, full: bool) -> None:
        profile = self.profile
        if not profile.requests:
            return
        try:
            for request in profile.requests:
                if request.rate == "slow" and not full:
                    continue
                if request.id in self.identity.failed_profile_requests and not full:
                    continue
                if not any(self._category_enabled(v.spec) for v in request.values):
                    continue
                try:
                    decoded = await self._run_profile_request(profile, request)
                except _REQUEST_ERRORS as err:
                    reason = f"{request.request}: {err}"
                    for value in request.values:
                        self.unavailable[value.spec.key] = reason
                    count = self._profile_failures.get(request.id, 0) + 1
                    self._profile_failures[request.id] = count
                    if count >= 3 and request.id not in self.identity.failed_profile_requests:
                        # Skip on fast cycles from now on, retried on full reads
                        self.identity.failed_profile_requests.append(request.id)
                    continue
                self._profile_failures.pop(request.id, None)
                if request.id in self.identity.failed_profile_requests:
                    self.identity.failed_profile_requests.remove(request.id)
                values.update(decoded)
        finally:
            await self._safe_reset_addressing()

    async def _run_profile_request(
        self, profile: VehicleProfile, request: ProfileRequest
    ) -> dict[str, Any]:
        await self.elm.run_init_commands((*profile.init, *request.init))
        messages = await self.elm.query(request.request)
        expected = bytes.fromhex(request.request[2:])
        message = next(
            (m for m in messages if m.data[1 : 1 + len(expected)] == expected),
            None,
        )
        if message is None:
            raise ResponseParseError(f"{request.request}: response for another identifier")
        raw = to_wican_bytes(message.data)
        decoded: dict[str, Any] = {}
        for value in request.values:
            try:
                result = value.formula.evaluate(raw)
            except ResponseParseError as err:
                self.unavailable[value.spec.key] = str(err)
                continue
            decoded[value.spec.key] = self._finish_value(value.spec, result, value.mapping)
            self.unavailable.pop(value.spec.key, None)
        return decoded

    @staticmethod
    def _finish_value(spec: ValueSpec, result: float, mapping: dict[int, str] | None) -> Any:
        if spec.binary:
            return bool(result)
        if mapping is not None:
            return mapping.get(int(result), "unknown")
        if spec.precision is not None:
            return round(result, spec.precision)
        if isinstance(result, float) and result.is_integer():
            return int(result)
        return result

    def _derive(self, values: dict[str, Any]) -> None:
        if "actual_torque_pct" in values and "reference_torque" in values:
            values["engine_torque"] = round(
                values["actual_torque_pct"] * values["reference_torque"] / 100
            )
        for derived in self.profile.derived:
            if derived.formula.names - values.keys():
                continue
            try:
                result = derived.formula.evaluate(values=values)
            except ResponseParseError:
                continue
            values[derived.spec.key] = self._finish_value(derived.spec, result, None)

    async def _safe_reset_addressing(self) -> None:
        try:
            await self.elm.reset_addressing()
        except (AdapterError, AdapterTimeout) as err:
            _LOGGER.debug("Restoring addressing failed: %s", err)

    # ------------------------------------------------------------------ DTC
    async def read_dtcs(self, *, permanent: bool, freeze_frame: bool) -> DtcReport:
        report = DtcReport()
        can = self.elm.bus in (BusKind.CAN_11, BusKind.CAN_29)
        for service, attr in ((0x03, "stored"), (0x07, "pending"), (0x0A, "permanent")):
            if service == 0x0A and not permanent:
                continue
            try:
                messages = await self.elm.query(f"{service:02X}")
            except NoDataError:
                setattr(report, attr, [])
                continue
            except (ResponseParseError, AdapterError) as err:
                _LOGGER.debug("Reading DTCs (service %02X) failed: %s", service, err)
                continue
            codes: list[str] = []
            for message in self._sorted(messages):
                codes.extend(decode_dtc_payload(message.data, can=can))
            setattr(report, attr, list(dict.fromkeys(codes)))
        if freeze_frame and report.stored:
            try:
                messages = await self.elm.query("020200")
                for message in self._sorted(messages):
                    if len(message.data) >= 5 and (message.data[3] or message.data[4]):
                        report.freeze_frame_dtc = format_dtc(message.data[3], message.data[4])
                        break
            except _REQUEST_ERRORS as err:
                _LOGGER.debug("Freeze frame not available: %s", err)
        return report

    async def ecu_awake(self) -> bool:
        """Cheap check whether the vehicle answers (0100)."""
        try:
            await self.elm.query("0100", timeout=4.0)
        except (NoDataError, VehicleUnreachable, ResponseParseError, AdapterError):
            return False
        return True

    # ---------------------------------------------------------------- helper
    @staticmethod
    def _sorted(messages: list[Message]) -> list[Message]:
        return sorted(messages, key=lambda m: m.ecu)

    def value_specs(self) -> dict[str, ValueSpec]:
        """All value specs that can be produced for the current vehicle."""
        specs: dict[str, ValueSpec] = {}
        for pid in self.identity.supported_pids:
            definition = PIDS.get(pid)
            if definition:
                for spec in definition.values:
                    specs[spec.key] = spec
        if 0x62 in self.identity.supported_pids and 0x63 in self.identity.supported_pids:
            specs.update({s.key: s for s in DERIVED_VALUES})
        specs.update(self.profile.value_specs)
        return {k: v for k, v in specs.items() if self._category_enabled(v) or v.category == CAT_DIAGNOSTIC}
