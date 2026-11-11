"""VIN validation and basic decoding (ISO 3779 / ISO 3780).

Only information that can be decoded reliably is returned: the manufacturer
(World Manufacturer Identifier) and – with a caveat – the model year. The model
itself is NOT encoded in a standardised way and is therefore not decoded.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass

_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")

#: World Manufacturer Identifiers (first 3 characters, or first 2 as prefix).
WMI: dict[str, str] = {
    # Volkswagen group
    "WVW": "Volkswagen", "WVG": "Volkswagen", "WV1": "Volkswagen", "WV2": "Volkswagen",
    "1VW": "Volkswagen", "3VW": "Volkswagen", "WAU": "Audi", "WA1": "Audi", "WUA": "Audi",
    "TRU": "Audi", "VSS": "SEAT", "TMB": "Škoda", "WP0": "Porsche",
    "WP1": "Porsche",
    # BMW group
    "WBA": "BMW", "WBS": "BMW", "WBY": "BMW", "4US": "BMW", "5UX": "BMW",
    "WMW": "MINI",
    # Mercedes-Benz
    "WDB": "Mercedes-Benz", "WDC": "Mercedes-Benz", "WDD": "Mercedes-Benz",
    "WDF": "Mercedes-Benz", "W1K": "Mercedes-Benz", "W1N": "Mercedes-Benz",
    "W1V": "Mercedes-Benz",
    # Toyota / Lexus
    "JTD": "Toyota", "JTE": "Toyota", "JTM": "Toyota", "JTN": "Toyota", "JTK": "Toyota",
    "SB1": "Toyota", "NMT": "Toyota", "VNK": "Toyota", "JTH": "Lexus", "JTJ": "Lexus",
    # Hyundai / Kia
    "KMH": "Hyundai", "TMA": "Hyundai", "NLH": "Hyundai", "5NP": "Hyundai",
    "KNA": "Kia", "KNC": "Kia", "KND": "Kia", "KNE": "Kia", "U5Y": "Kia", "U6Y": "Kia",
    # Renault / Nissan / Dacia
    "VF1": "Renault", "UU1": "Dacia",
    "JN1": "Nissan", "JN8": "Nissan", "SJN": "Nissan", "VSK": "Nissan", "5N1": "Nissan",
    # Tesla
    "5YJ": "Tesla", "7SA": "Tesla", "LRW": "Tesla", "XP7": "Tesla",
    # Ford
    "WF0": "Ford", "WF1": "Ford", "1FA": "Ford", "1FM": "Ford", "1FT": "Ford", "3FM": "Ford",
    # Stellantis
    "VF3": "Peugeot", "VR3": "Peugeot", "VF7": "Citroën", "VR7": "Citroën",
    "VXK": "Opel", "W0L": "Opel", "W0V": "Opel", "ZFA": "Fiat", "ZAR": "Alfa Romeo",
    "ZFF": "Ferrari", "1C4": "Jeep", "ZAC": "Jeep",
    # Others
    "YV1": "Volvo", "YV4": "Volvo", "LYV": "Volvo", "SAL": "Land Rover", "SAJ": "Jaguar",
    "JM1": "Mazda", "JMZ": "Mazda", "JHM": "Honda", "SHH": "Honda", "JF1": "Subaru",
    "JMB": "Mitsubishi", "TSM": "Suzuki", "JS2": "Suzuki", "LSJ": "MG",
    "LC0": "BYD", "YS3": "Saab", "WME": "smart", "LPS": "Polestar",
}

_YEAR_CODES = "ABCDEFGHJKLMNPRSTVWXY123456789"


@dataclass(frozen=True, slots=True)
class VinInfo:
    vin: str
    valid: bool
    manufacturer: str | None
    model_year: int | None
    #: True when the model year may be off by 30 years or not encoded at all
    model_year_uncertain: bool


def normalise_vin(raw: str) -> str:
    return "".join(ch for ch in raw.upper() if ch.isalnum())


def is_valid_vin(vin: str) -> bool:
    return bool(_VIN_RE.match(vin))


def decode_model_year(vin: str, today: dt.date | None = None) -> int | None:
    code = vin[9]
    if code not in _YEAR_CODES:
        return None
    base = 1980 + _YEAR_CODES.index(code)
    limit = (today or dt.date.today()).year + 1
    year = base
    while year + 30 <= limit:
        year += 30
    return year


def decode_vin(raw: str, today: dt.date | None = None) -> VinInfo:
    vin = normalise_vin(raw)
    valid = is_valid_vin(vin)
    manufacturer = WMI.get(vin[:3]) if len(vin) >= 3 else None
    year = decode_model_year(vin, today) if valid else None
    return VinInfo(
        vin=vin,
        valid=valid,
        manufacturer=manufacturer,
        model_year=year,
        # European manufacturers are not obliged to encode the year at position 10.
        model_year_uncertain=True,
    )


def vin_fingerprint(vin: str) -> str:
    """Short, non reversible identifier used to recognise a vehicle without storing it in logs."""
    return hashlib.sha256(vin.encode()).hexdigest()[:12]


def parse_vin_payload(payload: bytes) -> str | None:
    """Mode 09 PID 02 payload ``49 02 <count> <17 ascii bytes>`` -> VIN."""
    if len(payload) < 3 or payload[0] != 0x49 or payload[1] != 0x02:
        return None
    text = payload[3:].decode("ascii", errors="ignore")
    vin = normalise_vin(text)
    # Some ECUs pad with leading zero bytes / spaces – keep the last 17 chars.
    vin = vin[-17:] if len(vin) >= 17 else vin
    return vin if is_valid_vin(vin) else None
