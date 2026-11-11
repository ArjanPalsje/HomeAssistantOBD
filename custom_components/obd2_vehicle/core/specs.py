"""Description of a measurable value, independent of Home Assistant.

``device_class`` / ``state_class`` / ``entity_category`` hold the *string*
values of the corresponding Home Assistant enums so the core library stays
importable without Home Assistant.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Categories – used for the "enable sensor groups" options.
CAT_GENERAL = "general"
CAT_ENGINE = "engine"
CAT_EMISSIONS = "emissions"
CAT_EV = "ev"
CAT_DIAGNOSTIC = "diagnostic"
CAT_MANUFACTURER = "manufacturer"

CATEGORIES = (CAT_GENERAL, CAT_ENGINE, CAT_EMISSIONS, CAT_EV, CAT_DIAGNOSTIC, CAT_MANUFACTURER)


@dataclass(frozen=True, slots=True)
class ValueSpec:
    """Metadata for one value that can become an entity."""

    key: str
    name: str
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = "measurement"
    icon: str | None = None
    precision: int | None = None
    #: Realtime values (rpm, speed, current…) are only shown while a session is
    #: active. Non-realtime values (fuel level, SOC, odometer…) keep their last
    #: known value with a timestamp after the vehicle disconnects.
    realtime: bool = False
    category: str = CAT_GENERAL
    entity_category: str | None = None
    enabled_default: bool = True
    #: For enum sensors
    options: tuple[str, ...] | None = None
    #: binary values become binary_sensor entities
    binary: bool = False
    #: Localised names, e.g. {"nl": "Brandstofniveau"} (profiles only)
    translations: dict[str, str] = field(default_factory=dict)
    #: Translation key for built-in values
    translation_key: str | None = None
