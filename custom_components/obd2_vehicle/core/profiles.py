"""Vehicle profiles: manufacturer specific (extended) requests.

A profile is a JSON file in ``profiles/``. It declares which requests must be
sent to which ECU (via AT header/flow-control commands), how bytes are turned
into values (WiCAN formula notation, see :mod:`.expression`) and how the
profile is detected. Profiles only add values – generic OBD2 always keeps
working, also when no profile matches.

See ``docs/VEHICLE_PROFILES.md`` for the format.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from .elm327 import validate_command
from .expression import Formula
from .specs import CAT_MANUFACTURER, ValueSpec

_LOGGER = logging.getLogger(__name__)

PROFILE_DIR = Path(__file__).parent.parent / "profiles"

GENERIC_PROFILE_ID = "generic"

_SPEC_FIELDS = {
    "unit",
    "device_class",
    "state_class",
    "icon",
    "precision",
    "realtime",
    "entity_category",
    "enabled_default",
}


@dataclass(frozen=True, slots=True)
class ProfileValue:
    """One value decoded from a profile request."""

    spec: ValueSpec
    formula: Formula
    #: For enum values: raw number -> option
    mapping: dict[int, str] | None = None


@dataclass(frozen=True, slots=True)
class ProfileRequest:
    """One manufacturer specific request."""

    id: str
    request: str
    init: tuple[str, ...]
    values: tuple[ProfileValue, ...]
    #: "fast" requests run every cycle, "slow" ones only on full reads.
    rate: str = "fast"

    @property
    def service(self) -> int:
        return int(self.request[:2], 16)


@dataclass(frozen=True, slots=True)
class DerivedValue:
    """A value computed from other (already decoded) values."""

    spec: ValueSpec
    formula: Formula


@dataclass(slots=True)
class VehicleProfile:
    """A complete vehicle profile."""

    id: str
    name: str
    manufacturers: tuple[str, ...] = ()
    status: str = "generic"
    powertrain: str = "any"
    wmi: tuple[str, ...] = ()
    probe: str | None = None
    auto_detect: bool = True
    priority: int = 100
    init: tuple[str, ...] = ()
    requests: tuple[ProfileRequest, ...] = ()
    derived: tuple[DerivedValue, ...] = ()
    sources: tuple[str, ...] = ()
    notes: str = ""
    extends: str | None = None
    value_specs: dict[str, ValueSpec] = field(default_factory=dict)

    def request_by_id(self, request_id: str) -> ProfileRequest | None:
        return next((r for r in self.requests if r.id == request_id), None)


class ProfileError(ValueError):
    """Invalid profile file."""


def _spec_from_json(data: dict, *, default_realtime: bool) -> ValueSpec:
    key = data["key"]
    if not key.replace("_", "").isalnum() or not key.islower():
        raise ProfileError(f"Invalid value key {key!r}")
    kwargs = {k: data[k] for k in _SPEC_FIELDS if k in data}
    kwargs.setdefault("realtime", default_realtime)
    binary = bool(data.get("binary", False))
    options = None
    if "map" in data:
        options = tuple(dict.fromkeys([*data["map"].values(), "unknown"]))
        kwargs["device_class"] = "enum"
        kwargs["state_class"] = None
    if binary:
        kwargs["state_class"] = None
    translations = {lang: text for lang, text in data.get("names", {}).items()}
    return ValueSpec(
        key=key,
        name=data.get("name", key),
        category=data.get("category", CAT_MANUFACTURER),
        binary=binary,
        options=options,
        translations=translations,
        translation_key=data.get("translation_key"),
        **kwargs,
    )


def parse_profile(data: dict) -> VehicleProfile:
    """Validate and convert profile JSON."""
    try:
        profile_id = data["id"]
        init = tuple(data.get("init", ()))
        for cmd in init:
            validate_command(cmd)
        requests: list[ProfileRequest] = []
        specs: dict[str, ValueSpec] = {}
        for req in data.get("requests", ()):
            request = req["request"].replace(" ", "").upper()
            validate_command(request)  # raises for anything that is not read-only
            req_init = tuple(req.get("init", ()))
            for cmd in req_init:
                if not validate_command(cmd).startswith("AT"):
                    raise ProfileError(f"Request init may only contain AT commands: {cmd}")
            values: list[ProfileValue] = []
            for val in req["values"]:
                spec = _spec_from_json(val, default_realtime=False)
                mapping = None
                if "map" in val:
                    mapping = {int(k): v for k, v in val["map"].items()}
                values.append(ProfileValue(spec, Formula(val["formula"]), mapping))
                specs[spec.key] = spec
            requests.append(
                ProfileRequest(
                    id=req.get("id", request),
                    request=request,
                    init=req_init,
                    values=tuple(values),
                    rate=req.get("rate", "fast"),
                )
            )
        derived: list[DerivedValue] = []
        for val in data.get("derived", ()):
            spec = _spec_from_json(val, default_realtime=False)
            formula = Formula(val["formula"])
            unknown = formula.names - set(specs)
            if unknown:
                raise ProfileError(f"Derived value {spec.key} uses unknown values {unknown}")
            derived.append(DerivedValue(spec, formula))
            specs[spec.key] = spec
        detect = data.get("detect", {})
        return VehicleProfile(
            id=profile_id,
            name=data["name"],
            manufacturers=tuple(data.get("manufacturers", ())),
            status=data.get("status", "community"),
            powertrain=data.get("powertrain", "any"),
            wmi=tuple(w.upper() for w in detect.get("wmi", ())),
            probe=detect.get("probe"),
            auto_detect=bool(detect.get("auto", True)),
            priority=int(detect.get("priority", 100)),
            init=init,
            requests=tuple(requests),
            derived=tuple(derived),
            sources=tuple(data.get("sources", ())),
            notes=data.get("notes", ""),
            extends=data.get("extends"),
            value_specs=specs,
        )
    except (KeyError, TypeError) as err:
        raise ProfileError(f"Invalid profile {data.get('id', '?')}: {err!r}") from err
    except ValueError as err:
        raise ProfileError(f"Invalid profile {data.get('id', '?')}: {err}") from err


def _merge(base: VehicleProfile, child: VehicleProfile) -> VehicleProfile:
    """Child profile extends base: child requests with the same id replace base ones."""
    child_ids = {r.id for r in child.requests}
    requests = tuple(r for r in base.requests if r.id not in child_ids) + child.requests
    derived_keys = {d.spec.key for d in child.derived}
    derived = tuple(d for d in base.derived if d.spec.key not in derived_keys) + child.derived
    specs = {**base.value_specs, **child.value_specs}
    return VehicleProfile(
        id=child.id,
        name=child.name,
        manufacturers=child.manufacturers or base.manufacturers,
        status=child.status,
        powertrain=child.powertrain if child.powertrain != "any" else base.powertrain,
        wmi=child.wmi or base.wmi,
        probe=child.probe or base.probe,
        auto_detect=child.auto_detect,
        priority=child.priority,
        init=child.init or base.init,
        requests=requests,
        derived=derived,
        sources=base.sources + tuple(s for s in child.sources if s not in base.sources),
        notes=child.notes or base.notes,
        extends=base.extends,
        value_specs=specs,
    )


GENERIC_PROFILE = VehicleProfile(
    id=GENERIC_PROFILE_ID,
    name="Generic OBD2 (SAE J1979)",
    status="standard",
    notes="Only standardised OBD2 data. Works with every OBD2 compliant vehicle.",
)


class ProfileRegistry:
    """All available vehicle profiles."""

    def __init__(self) -> None:
        self._profiles: dict[str, VehicleProfile] = {GENERIC_PROFILE_ID: GENERIC_PROFILE}
        self.errors: dict[str, str] = {}

    def load_directory(self, directory: Path = PROFILE_DIR) -> None:
        """Load all ``*.json`` profiles (blocking I/O – run in an executor)."""
        raw: dict[str, VehicleProfile] = {}
        for path in sorted(directory.glob("*.json")):
            try:
                with path.open(encoding="utf-8") as fh:
                    profile = parse_profile(json.load(fh))
            except (OSError, json.JSONDecodeError, ProfileError) as err:
                self.errors[path.name] = str(err)
                _LOGGER.error("Vehicle profile %s ignored: %s", path.name, err)
                continue
            raw[profile.id] = profile
        for profile_id, profile in raw.items():
            seen = {profile_id}
            merged = profile
            while merged.extends:
                base = raw.get(merged.extends)
                if base is None or base.id in seen:
                    self.errors[profile_id] = f"extends unknown/cyclic profile {merged.extends}"
                    merged = None  # type: ignore[assignment]
                    break
                seen.add(base.id)
                merged = _merge(base, merged)
            if merged is not None:
                self._profiles[profile_id] = merged

    def add(self, profile: VehicleProfile) -> None:
        self._profiles[profile.id] = profile

    def get(self, profile_id: str | None) -> VehicleProfile:
        return self._profiles.get(profile_id or GENERIC_PROFILE_ID, GENERIC_PROFILE)

    def __contains__(self, profile_id: str) -> bool:
        return profile_id in self._profiles

    @property
    def profiles(self) -> list[VehicleProfile]:
        return sorted(self._profiles.values(), key=lambda p: (p.id != GENERIC_PROFILE_ID, p.name))

    def candidates_for_vin(self, vin: str | None) -> list[VehicleProfile]:
        """Profiles whose WMI list matches the VIN, in file order."""
        if not vin or len(vin) < 3:
            return []
        wmi = vin[:3].upper()
        matches = [
            p
            for p in self._profiles.values()
            if p.probe and p.auto_detect and any(wmi.startswith(w) for w in p.wmi)
        ]
        return sorted(matches, key=lambda p: (p.priority, p.id))
