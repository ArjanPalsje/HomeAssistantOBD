# Voertuigprofielen toevoegen

Een profiel is één JSON-bestand in `custom_components/obd2_vehicle/profiles/`. Het wordt
bij het starten geladen en gevalideerd; een fout profiel wordt overgeslagen (zie log en
diagnostiek → `profile_errors`). Generieke OBD2 blijft altijd werken.

## Regels

1. **Alleen leesverzoeken.** Toegestaan: `21xx` (KWP ReadDataByLocalIdentifier) en `22xxxx`
   (UDS ReadDataByIdentifier). Alles anders wordt bij het laden geweigerd.
2. **Alleen onderbouwde formules.** Neem uitsluitend commando's over uit een
   gedocumenteerde bron en vermeld die in `sources`. Spreken bronnen elkaar tegen: weglaten.
3. `init`-regels mogen alleen AT-commando's uit de whitelist bevatten (`ATSH`, `ATCP`,
   `ATCRA`, `ATFCSH`, `ATFCSD`, `ATFCSM`, `ATSP`, `ATST`, `ATCAF`, `ATCFC`, `ATCEA`, …).
   Na de profielverzoeken herstelt de integratie automatisch adressering en protocol.

## Formaat

```json
{
  "id": "merk_model",
  "name": "Merk Model (platform)",
  "manufacturers": ["Merk"],
  "powertrain": "electric",
  "status": "community",
  "detect": { "wmi": ["ABC"], "probe": "bms", "priority": 50, "auto": true },
  "init": ["ATST96"],
  "requests": [
    {
      "id": "bms",
      "request": "220101",
      "init": ["ATSH7E4"],
      "rate": "fast",
      "values": [
        {
          "key": "hv_soc",
          "formula": "B10/2",
          "name": "HV battery state of charge (BMS)",
          "names": { "nl": "HV-accu laadtoestand (BMS)" },
          "unit": "%", "device_class": "battery", "precision": 1, "category": "ev"
        }
      ]
    }
  ],
  "derived": [
    { "key": "hv_power", "formula": "hv_voltage*hv_current/1000", "name": "HV battery power",
      "unit": "kW", "device_class": "power", "realtime": true, "category": "ev" }
  ],
  "sources": ["https://…"],
  "notes": "Beperkingen, tekenconventies, validatiestatus"
}
```

| Veld | Betekenis |
|---|---|
| `detect.wmi` | Eerste tekens van het VIN waarvoor het profiel geprobeerd wordt |
| `detect.probe` | `id` van het verzoek dat bij autodetectie gestuurd wordt; antwoordt het voertuig met geldige data, dan wordt het profiel gekozen |
| `detect.priority` | Lager = eerder proberen |
| `detect.auto` | `false` = alleen handmatig te kiezen |
| `extends` | Erf alle verzoeken van een ander profiel; verzoeken met hetzelfde `id` worden vervangen |
| `rate` | `fast` = elke uitleescyclus, `slow` = alleen bij volledige uitlezing |
| `realtime` | `true` = waarde wordt onbeschikbaar als er geen sessie actief is |
| `binary` | `true` = wordt een `binary_sensor` |
| `map` | Enum: `{"1": "driving", "4": "ac_charging"}` |
| `category` | `general`, `engine`, `emissions`, `ev`, `manufacturer` (aan/uit in opties) |

### Formulenotatie (WiCAN-compatibel)

| Notatie | Betekenis |
|---|---|
| `B10` | byte 10 (unsigned) |
| `S17` | byte 17 (signed) |
| `[B19:B20]` | big-endian unsigned over bytes 19–20 |
| `[S11:S14]` | big-endian signed |
| `B15:7` | bit 7 van byte 15 |
| `+ - * / // % << >> & \| ^`, `min() max() abs() round()` | rekenen |

**Byte-indexering** volgt WiCAN: de ruwe ISO-TP-framebytes zonder CAN-ID. Bij een single
frame is `B0` de lengte, `B1` = `0x62`, `B2-B3` de DID en `B4` het eerste databyte. Bij
multi-frame is elke 8e byte (`B8`, `B16`, …) een PCI-byte. Formules uit WiCAN-profielen kunnen
daardoor letterlijk overgenomen worden. Gebruik je een evDash/Torque-offset, reken die dan om.

Afgeleide waarden (`derived`) gebruiken de `key`s van eerder gedecodeerde waarden.

## Standaard sleutels

Gebruik waar mogelijk deze sleutels, zodat dashboards en automatiseringen merkonafhankelijk
werken: `hv_soc`, `hv_soc_display`, `hv_soh`, `hv_voltage`, `hv_current` (positief = ontladen),
`hv_power`, `charging_power`, `hv_temp`, `hv_temp_min`, `hv_temp_max`, `aux_battery_voltage`
(12V-accu), `odometer`, `charging`, `charging_dc`, `ac_plug`, `energy_charged_total`,
`energy_discharged_total`.

## Testen

1. Voeg een test toe in `tests/test_reader_profiles.py` met een `SimulatedVehicle` waarvan
   `extended[(header, request)]` een payload bevat (gebruik `wican_payload()` om bytes op
   WiCAN-indices te zetten).
2. `pytest -q` controleert ook automatisch dat elk profiel laadt, alleen leesverzoeken bevat
   en bronnen vermeldt.
3. Valideer op het voertuig met `python scripts/ble_probe.py <adres> --vehicle`.
