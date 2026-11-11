"""Generate strings.json / translations for the integration."""
import json
import os
import sys

root = sys.argv[1]  # custom_components dir
sys.path.insert(0, os.path.join(root, "obd2_vehicle"))
from core.pids import FUEL_TYPES, all_value_specs  # noqa: E402

NL_VALUES = {
    "mil_on": "Storingslampje (MIL)",
    "dtc_count_ecu": "Aantal foutcodes (volgens ECU)",
    "engine_load": "Motorbelasting",
    "coolant_temp": "Koelvloeistoftemperatuur",
    "short_fuel_trim_1": "Korte brandstofcorrectie bank 1",
    "long_fuel_trim_1": "Lange brandstofcorrectie bank 1",
    "short_fuel_trim_2": "Korte brandstofcorrectie bank 2",
    "long_fuel_trim_2": "Lange brandstofcorrectie bank 2",
    "fuel_pressure": "Brandstofdruk",
    "intake_pressure": "Inlaatspruitstukdruk",
    "rpm": "Toerental",
    "speed": "Snelheid",
    "timing_advance": "Ontstekingsvervroeging",
    "intake_temp": "Inlaatluchttemperatuur",
    "maf": "Luchtmassameting (MAF)",
    "throttle_position": "Gasklepstand",
    "run_time": "Looptijd sinds motorstart",
    "distance_with_mil": "Afstand met storingslampje aan",
    "fuel_rail_pressure": "Brandstofraildruk",
    "commanded_egr": "Gevraagde EGR",
    "fuel_level": "Brandstofniveau",
    "warmups_since_clear": "Opwarmcycli sinds wissen codes",
    "distance_since_clear": "Afstand sinds wissen codes",
    "barometric_pressure": "Barometrische druk",
    "catalyst_temp_b1s1": "Katalysatortemperatuur bank 1 sensor 1",
    "control_module_voltage": "Boordspanning ECU (12V-systeem)",
    "absolute_load": "Absolute belasting",
    "commanded_lambda": "Gevraagde lambda",
    "relative_throttle": "Relatieve gasklepstand",
    "ambient_temp": "Buitentemperatuur",
    "accelerator_pedal_d": "Gaspedaalpositie D",
    "accelerator_pedal_e": "Gaspedaalpositie E",
    "commanded_throttle": "Gevraagde gasklepstand",
    "time_with_mil": "Looptijd met storingslampje aan",
    "time_since_clear": "Tijd sinds wissen codes",
    "fuel_type": "Brandstoftype",
    "ethanol_percent": "Ethanolpercentage",
    "relative_accelerator": "Relatieve gaspedaalpositie",
    "hybrid_battery_remaining": "Hybride/EV-accupakket resterend",
    "oil_temp": "Motorolietemperatuur",
    "fuel_rate": "Brandstofverbruik (debiet)",
    "driver_demand_torque": "Gevraagd motorkoppel",
    "actual_torque_pct": "Actueel motorkoppel",
    "reference_torque": "Referentiekoppel motor",
    "odometer": "Kilometerstand",
    "engine_torque": "Motorkoppel",
}
FUEL_NL = {
    "not_available": "Niet beschikbaar", "gasoline": "Benzine", "methanol": "Methanol", "ethanol": "Ethanol",
    "diesel": "Diesel", "lpg": "LPG", "cng": "CNG", "propane": "Propaan", "electric": "Elektrisch",
    "bifuel_gasoline": "Bi-fuel (benzine)", "bifuel_methanol": "Bi-fuel (methanol)",
    "bifuel_ethanol": "Bi-fuel (ethanol)", "bifuel_lpg": "Bi-fuel (LPG)", "bifuel_cng": "Bi-fuel (CNG)",
    "bifuel_propane": "Bi-fuel (propaan)", "bifuel_electricity": "Bi-fuel (elektrisch)",
    "bifuel_electric_combustion": "Bi-fuel (elektrisch + verbranding)", "hybrid_gasoline": "Hybride benzine",
    "hybrid_ethanol": "Hybride ethanol", "hybrid_diesel": "Hybride diesel", "hybrid_electric": "Hybride elektrisch",
    "hybrid_electric_combustion": "Hybride (elektrisch + verbranding)",
    "hybrid_regenerative": "Hybride regeneratief", "bifuel_diesel": "Bi-fuel (diesel)",
}
FUEL_EN = {k: k.replace("_", " ").capitalize().replace("Lpg", "LPG").replace("Cng", "CNG").replace(" lpg", " LPG").replace(" cng", " CNG") for k in FUEL_TYPES.values()}

CONN_STATES = {
    "not_in_range": ("Not in range", "Niet binnen bereik"),
    "idle": ("Idle", "Inactief"),
    "connecting": ("Connecting", "Verbinden"),
    "reading": ("Reading", "Uitlezen"),
    "connected": ("Connected", "Verbonden"),
    "vehicle_off": ("Vehicle off / not responding", "Voertuig uit / reageert niet"),
    "error": ("Error", "Fout"),
}
VEHICLE_SENSORS = {
    "connection_state": ("Connection state", "Verbindingsstatus"),
    "last_read": ("Last successful read", "Laatste succesvolle uitlezing"),
    "last_full_read": ("Last full read", "Laatste volledige uitlezing"),
    "last_connected": ("Last connection", "Laatste verbinding"),
    "adapter_voltage": ("12 V battery (measured by adapter)", "12V-accu (gemeten door dongle)"),
    "dtc_stored": ("Stored fault codes", "Opgeslagen foutcodes"),
    "dtc_pending": ("Pending fault codes", "Pending foutcodes"),
    "dtc_permanent": ("Permanent fault codes", "Permanente foutcodes"),
    "vin": ("VIN", "VIN (chassisnummer)"),
    "protocol": ("OBD protocol", "OBD-protocol"),
    "adapter": ("Adapter", "Dongle"),
    "vehicle_profile": ("Vehicle profile", "Voertuigprofiel"),
    "supported_pids": ("Supported PIDs", "Ondersteunde PID's"),
    "last_error": ("Last error", "Laatste fout"),
    "rssi": ("Signal strength", "Signaalsterkte"),
}
BINARY = {
    "adapter_present": ("Adapter in range", "Dongle binnen bereik"),
    "connected": ("Connected", "Verbonden"),
    "vehicle_responding": ("Vehicle responding", "Voertuig reageert"),
    "engine_running": ("Engine running", "Motor draait"),
    "fault_codes_present": ("Fault codes present", "Foutcodes aanwezig"),
}
BUTTONS = {
    "read_now": ("Read now (full)", "Nu volledig uitlezen"),
    "reconnect": ("Reconnect", "Opnieuw verbinden"),
    "reidentify": ("Identify vehicle again", "Voertuig opnieuw identificeren"),
}


def build(lang: str) -> dict:
    i = 0 if lang == "en" else 1
    nl = lang == "nl"
    sensors: dict = {}
    binary: dict = {}
    for spec in all_value_specs():
        name = NL_VALUES[spec.key] if nl else spec.name
        target = binary if spec.binary else sensors
        entry = {"name": name}
        if spec.key == "fuel_type":
            entry["state"] = FUEL_NL if nl else FUEL_EN
        target[spec.key] = entry
    for key, names in VEHICLE_SENSORS.items():
        entry = {"name": names[i]}
        if key == "connection_state":
            entry["state"] = {k: v[i] for k, v in CONN_STATES.items()}
        if key.startswith("dtc_"):
            entry["state_attributes"] = {
                "codes": {"name": "Codes"},
                "freeze_frame_dtc": {"name": "Freeze frame DTC" if not nl else "Freeze-frame-foutcode"},
            }
        sensors[key] = entry
    for key, names in BINARY.items():
        binary[key] = {"name": names[i]}
    buttons = {k: {"name": v[i]} for k, v in BUTTONS.items()}

    t = (lambda en, nlt: nlt if nl else en)
    mode_options = {
        "snapshot": t("Snapshot – read once after arrival", "Snapshot – eenmalig uitlezen na aankomst"),
        "live": t("Live – read continuously while the vehicle answers", "Live – continu uitlezen zolang het voertuig antwoordt"),
        "smart": t("Smart – automatic (recommended)", "Slim – automatisch (aanbevolen)"),
    }
    categories = {
        "general": t("General (fuel, speed, odometer, MIL)", "Algemeen (brandstof, snelheid, km-stand, MIL)"),
        "engine": t("Engine", "Motor"),
        "emissions": t("Emissions / fuel trims", "Emissies / brandstofcorrecties"),
        "ev": t("Electric / hybrid", "Elektrisch / hybride"),
        "manufacturer": t("Manufacturer specific (profile)", "Merkspecifiek (profiel)"),
    }
    vehicle_fields = {
        "vehicle_name": t("Vehicle name", "Voertuignaam"),
        "profile": t("Vehicle profile", "Voertuigprofiel"),
        "mode": t("Read mode", "Uitleesmodus"),
        "bluetooth_source": t("Bluetooth adapter / proxy", "Bluetooth-adapter / proxy"),
        "skip_connection_test": t("Skip connection test", "Verbindingstest overslaan"),
    }
    options_fields = {
        "vehicle_name": vehicle_fields["vehicle_name"],
        "bluetooth_source": vehicle_fields["bluetooth_source"],
        "mode": vehicle_fields["mode"],
        "profile": vehicle_fields["profile"],
        "live_interval": t("Live interval", "Live-interval"),
        "charging_interval": t("Interval while charging", "Interval tijdens laden"),
        "parked_interval": t("Interval while parked with ignition on", "Interval geparkeerd met contact aan"),
        "probe_interval": t("Wake-up check interval (0 = only on arrival)", "Controle-interval slapend voertuig (0 = alleen bij aankomst)"),
        "full_read_interval": t("Full read interval (slow values, fault codes)", "Interval volledige uitlezing (trage waarden, foutcodes)"),
        "categories": t("Sensor groups", "Sensorgroepen"),
        "advanced_diagnostics": t("Advanced diagnostics (permanent DTCs, freeze frame)", "Geavanceerde diagnostiek (permanente foutcodes, freeze frame)"),
        "log_traffic": t("Log raw OBD traffic (debug log)", "Ruwe OBD-communicatie loggen (debuglog)"),
        "command_timeout": t("Command timeout", "Timeout per commando"),
    }
    bt_source_desc = t(
            "'Automatic' lets Home Assistant pick the adapter or ESPHome proxy with the best signal. Choose a specific one to always connect through it (the value behind it is the signal strength to the dongle).",
            "Bij 'Automatisch' kiest Home Assistant de adapter of ESPHome-proxy met het beste signaal. Kies een specifieke om altijd daarlangs te verbinden (de waarde erachter is de signaalsterkte naar de dongle).",
        )
    options_desc = {
        "bluetooth_source": bt_source_desc,
        "probe_interval": t(
            "Every request may wake control units of a parked vehicle. Keep this long or 0.",
            "Elk verzoek kan regeleenheden van een geparkeerd voertuig wekken. Houd dit lang of 0.",
        ),
        "live_interval": t("Used while driving / in live mode.", "Gebruikt tijdens rijden / in live-modus."),
        "categories": t("Individual entities can also be disabled in the entity settings.", "Losse entiteiten kun je ook uitschakelen bij de entiteitsinstellingen."),
    }
    return {
        "config": {
            "flow_title": "{name}",
            "step": {
                "user": {
                    "title": t("Add OBD2 adapter", "OBD2-dongle toevoegen"),
                    "description": t(
                        "⚠️ This integration is under development and is used entirely at your own risk; the author accepts no liability for any direct or indirect damage (see README). Each adapter belongs to one vehicle. Add the integration again for every additional adapter/vehicle.",
                        "⚠️ Deze integratie is nog in ontwikkeling en gebruik is geheel op eigen risico; de auteur aanvaardt geen aansprakelijkheid voor directe of indirecte schade (zie README). Elke dongle hoort bij één voertuig. Voeg de integratie opnieuw toe voor elke extra dongle/auto.",
                    ),
                    "menu_options": {
                        "pick_bluetooth": t("Bluetooth LE adapter (e.g. Vgate iCar Pro 2S)", "Bluetooth LE-dongle (bijv. Vgate iCar Pro 2S)"),
                        "tcp": t("Wi-Fi adapter (TCP)", "Wi-Fi-dongle (TCP)"),
                        "simulator": t("Simulator (test without vehicle)", "Simulator (testen zonder auto)"),
                    },
                },
                "pick_bluetooth": {
                    "title": t("Choose Bluetooth adapter", "Kies Bluetooth-dongle"),
                    "description": t(
                        "Devices marked ★ are recognised OBD2 adapters. The Vgate iCar Pro 2S advertises as 'IOS-Vlink'. Already configured adapters are hidden. Turn the ignition on to wake the adapter if it is missing.",
                        "Apparaten met ★ zijn herkende OBD2-dongles. De Vgate iCar Pro 2S heet 'IOS-Vlink'. Al gekoppelde dongles worden niet getoond. Zet het contact aan om de dongle te wekken als hij ontbreekt.",
                    ),
                    "data": {"address": t("Adapter", "Dongle")},
                },
                "bluetooth_confirm": {
                    "title": t("OBD2 adapter found", "OBD2-dongle gevonden"),
                    "description": t("Set up {name} ({model})?", "{name} ({model}) instellen?"),
                },
                "tcp": {
                    "title": t("Wi-Fi OBD2 adapter", "Wi-Fi OBD2-dongle"),
                    "data": {"host": t("Host", "Host"), "port": t("Port", "Poort")},
                },
                "simulator": {
                    "title": "Simulator",
                    "data": {"sim_vehicle": t("Simulated vehicle", "Gesimuleerd voertuig")},
                },
                "vehicle": {
                    "title": t("Vehicle", "Voertuig"),
                    "description": t(
                        "The connection test only checks the adapter, not the vehicle. With 'Automatic' the profile is chosen from the VIN; generic OBD2 always works.",
                        "De verbindingstest controleert alleen de dongle, niet het voertuig. Bij 'Automatisch' wordt het profiel op basis van het VIN gekozen; generieke OBD2 werkt altijd.",
                    ),
                    "data": vehicle_fields,
                    "data_description": {"bluetooth_source": bt_source_desc},
                },
            },
            "error": {
                "cannot_connect": t("Cannot connect to the adapter. Is it awake and in range (and not connected to a phone)?", "Kan niet verbinden met de dongle. Is hij wakker en binnen bereik (en niet verbonden met een telefoon)?"),
                "not_seen_by_adapter": t("The selected Bluetooth adapter/proxy does not see this dongle at the moment. Choose another one or 'Automatic'.", "De gekozen Bluetooth-adapter/proxy ziet deze dongle op dit moment niet. Kies een andere of 'Automatisch'."),
                "not_elm327": t("The device does not answer like an ELM327 adapter.", "Het apparaat antwoordt niet als een ELM327-dongle."),
                "unknown": t("Unexpected error", "Onverwachte fout"),
            },
            "abort": {
                "already_configured": t("This adapter is already configured.", "Deze dongle is al gekoppeld."),
                "already_in_progress": t("Setup is already in progress.", "Instellen is al bezig."),
                "no_devices_found": t("No new Bluetooth devices found. Wake the adapter (ignition on) and try again.", "Geen nieuwe Bluetooth-apparaten gevonden. Wek de dongle (contact aan) en probeer opnieuw."),
            },
        },
        "options": {
            "step": {
                "init": {
                    "title": t("OBD2 vehicle options", "OBD2-voertuigopties"),
                    "data": options_fields,
                    "data_description": options_desc,
                }
            }
        },
        "selector": {
            "mode": {"options": mode_options},
            "categories": {"options": categories},
            "sim_vehicle": {"options": {"combustion": t("Petrol car", "Benzineauto"), "electric": t("Electric car (Hyundai/Kia profile)", "Elektrische auto (Hyundai/Kia-profiel)")}},
        },
        "entity": {"sensor": sensors, "binary_sensor": binary, "button": buttons},
    }


base = os.path.join(root, "obd2_vehicle")
os.makedirs(os.path.join(base, "translations"), exist_ok=True)
en = build("en")
for path, data in (("strings.json", en), ("translations/en.json", en), ("translations/nl.json", build("nl"))):
    with open(os.path.join(base, path), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
print("ok")
