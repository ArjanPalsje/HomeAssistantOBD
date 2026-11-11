"""Known BLE OBD2 adapters and their GATT layouts.

Sources (see docs/RESEARCH.md for the full research notes):

* Vgate iCar Pro (BLE 4.0) / iCar Pro 2S: GATT dump of an iCar Pro BLE shows two
  serial services on the same device (Feasycom FSC-BT826N module):
  - ``e7810a71-73ae-499d-8c15-faa9aef0c3f2`` with one characteristic
    ``bef8d6c9-9c21-4c9e-b632-bd58c1009f9f`` (notify + write/write-no-response)
  - ``000018f0-0000-1000-8000-00805f9b34fb`` with ``00002af0`` (notify) and
    ``00002af1`` (write). evDash uses this pair as default "for VGate iCar Pro
    BLE4 adapter" (src/BoardInterface.cpp) and a Home Assistant user reported
    it working with the iCar Pro 2S.
  BLE name: ``IOS-Vlink`` (the Classic SPP side is ``Android-Vlink``, PIN 1234).
* vLinker MC+/FS/MS (BLE firmware): ``vLinker MC-IOS`` etc., same E7810A71 layout.
* Generic BLE clones: ``FFF0`` (FFF1 notify / FFF2 write) or ``FFE0`` (FFE1
  notify + write).

The transport tries the layouts in this order and falls back to a heuristic
(a vendor service that has a notify characteristic and a write characteristic).
"""

from __future__ import annotations

from dataclasses import dataclass


def _uuid16(short: str) -> str:
    return f"0000{short.lower()}-0000-1000-8000-00805f9b34fb"


@dataclass(frozen=True, slots=True)
class GattLayout:
    """Service / characteristic combination carrying the ELM327 byte stream."""

    name: str
    service: str
    notify: str
    write: str


GATT_LAYOUTS: tuple[GattLayout, ...] = (
    GattLayout(
        name="Vgate (Feasycom vendor service)",
        service="e7810a71-73ae-499d-8c15-faa9aef0c3f2",
        notify="bef8d6c9-9c21-4c9e-b632-bd58c1009f9f",
        write="bef8d6c9-9c21-4c9e-b632-bd58c1009f9f",
    ),
    GattLayout(
        name="Vgate / generic 18F0",
        service=_uuid16("18f0"),
        notify=_uuid16("2af0"),
        write=_uuid16("2af1"),
    ),
    GattLayout(
        name="Generic FFF0",
        service=_uuid16("fff0"),
        notify=_uuid16("fff1"),
        write=_uuid16("fff2"),
    ),
    GattLayout(
        name="Generic FFE0",
        service=_uuid16("ffe0"),
        notify=_uuid16("ffe1"),
        write=_uuid16("ffe1"),
    ),
)

#: Standard Bluetooth SIG services that never carry the serial stream.
IGNORED_SERVICES = frozenset(
    _uuid16(s) for s in ("1800", "1801", "180a", "180f", "1812", "fe59")
)


@dataclass(frozen=True, slots=True)
class AdapterModel:
    """A BLE adapter model that is recognised from its advertisement."""

    model: str
    manufacturer: str
    name_prefixes: tuple[str, ...]
    support: str  # "verified-design", "community", "generic"
    notes: str


ADAPTER_MODELS: tuple[AdapterModel, ...] = (
    AdapterModel(
        model="iCar Pro 2S / iCar Pro BLE",
        manufacturer="Vgate",
        name_prefixes=("IOS-Vlink",),
        support="verified-design",
        notes=(
            "Primary target. Dual mode (Classic SPP as 'Android-Vlink', BLE as "
            "'IOS-Vlink'); only the BLE side is used. Reports 'ELM327 v2.3'."
        ),
    ),
    AdapterModel(
        model="vLinker (BLE firmware)",
        manufacturer="Vgate",
        name_prefixes=("vLinker",),
        support="community",
        notes="vLinker FS/MS must run the 'BLE+BT' firmware to be visible over BLE.",
    ),
    AdapterModel(
        model="Generic ELM327 BLE",
        manufacturer="Unknown",
        name_prefixes=("OBDII", "OBD2", "OBDBLE", "V-LINK", "VEEPEAK", "Android-Vlink"),
        support="generic",
        notes="Clones vary a lot; GATT layout is detected at connect time.",
    ),
)


def identify_adapter(local_name: str | None) -> AdapterModel | None:
    """Return the adapter model for an advertised local name."""
    if not local_name:
        return None
    lname = local_name.lower()
    for model in ADAPTER_MODELS:
        if any(lname.startswith(p.lower()) for p in model.name_prefixes):
            return model
    return None


def is_known_service(uuid: str) -> bool:
    """True when ``uuid`` is one of the known serial services."""
    return uuid.lower() in {layout.service for layout in GATT_LAYOUTS}
