# Fase 2 – Architectuur

```
custom_components/obd2_vehicle/
├── core/                    ← puur Python, géén Home Assistant-imports (los testbaar)
│   ├── transport/
│   │   ├── base.py          1. Transport-interface (bytes in/uit)
│   │   ├── ble.py           1. BLE (bleak + bleak-retry-connector, GATT-layoutdetectie)
│   │   └── tcp.py           1. Wi-Fi ELM327
│   ├── adapters.py          2. Bekende dongles + GATT-layouts (Vgate e.a.)
│   ├── elm327.py            3. ELM327-driver: lock, prompt, timeouts, resync, read-only whitelist
│   ├── protocol.py          3. Frameparser, ISO-TP-reassemblage, CAN 11/29 bit + legacy
│   ├── pids.py              4. SAE J1979 Mode 01-definities en conversies
│   ├── dtc.py / vin.py      4. Foutcodes en VIN-decodering
│   ├── expression.py        5. Veilige formule-evaluator (WiCAN-notatie)
│   ├── profiles.py          5. Profiellader, overerving, auto-detectie
│   ├── reader.py            6. Uitleessessie: protocol, VIN, PID-ontdekking, profiel, waarden, DTC's
│   ├── scheduler.py         6. Planning: snapshot/live/slim, backoff, intervallen (pure functie)
│   └── simulator.py         ELM327 + voertuigsimulator (tests en demo-modus)
├── profiles/*.json          5. Voertuigprofielen
├── coordinator.py           6. Sessiebeheer per dongle (verbinding, aanwezigheid, opslag)
├── sensor.py / binary_sensor.py / button.py / entity.py   7. Entiteiten
├── config_flow.py           8. Config- en optionsflow
└── diagnostics.py           8. Diagnostiek-export (geredigeerd)
```

## Eén dongle = één config entry = één voertuig

Elke gekoppelde dongle krijgt een eigen config entry met een eigen coordinator, transport,
ELM327-driver (met eigen `asyncio.Lock`), opgeslagen identiteit (VIN, protocol, ondersteunde
PID's, profiel) en één apparaat in Home Assistant. Meerdere dongles/auto's werken volledig
onafhankelijk en tegelijk. Al gekoppelde dongles worden niet meer in de keuzelijst getoond.
Wordt een dongle in een andere auto gestoken, dan detecteert de integratie het nieuwe VIN en
bepaalt alle mogelijkheden opnieuw.

## Sessieverloop

```
advertentie / timer / knop
        │
   scheduler.plan()  ──► wachten (event of timeout)
        │ run
   transport.connect ──► ELM327.initialize (ATZ, ATE0, ATL0, ATS0, ATH1, ATAT1, ATSP0)
        │                 └─ geen antwoord → volgende GATT-layout
   reader.start      ──► 0100 + ATDPN (protocol), 0902 (VIN), 0100/0120/… (bitmaps), profielprobe
        │
   reader.read_values ─► Mode 01 in batches van 6 PID's (CAN), terugval op losse PID's,
        │                 profielverzoeken (ATSH/ATCRA/ATFCSH …), afgeleide waarden
   (volledig) read_dtcs ► 03, 07, (0A, 020200 bij geavanceerd)
        │
   publiceren + opslaan ─► verbinding verbreken tenzij de volgende uitlezing < 60 s is
```

Fouten:

* `VehicleUnreachable`/`NoData` (contact uit) → status *voertuig uit*, realtime sensoren
  onbeschikbaar, verbinding dicht, opnieuw proberen volgens probe-interval.
* `TransportUnavailable`/timeouts → exponentiële backoff (10 s → 15 min).
* Corrupte frames van één ECU → alleen die ECU wordt genegeerd; ontbrekende profielwaarden
  krijgen een `unavailable_reason`.
* Elke sessie heeft een harde timeout (180 s); een onbereikbaar voertuig blokkeert nooit HA.

## Realtime versus laatst bekend

| Soort | Voorbeelden | Na verbreken |
|---|---|---|
| realtime | toerental, snelheid, stroom, vermogen, temperaturen | **onbeschikbaar** |
| laatst bekend | brandstof, SOC, SOH, kilometerstand, foutcodes | blijft staan, attribuut `data_status: last_known` |

Daarnaast: `sensor.<voertuig>_laatste_succesvolle_uitlezing` (timestamp). Laatst bekende
waarden worden opgeslagen (`.storage/obd2_vehicle.<entry_id>`) en overleven een herstart.
Attributen die vaak wijzigen worden niet door de Recorder opgeslagen.

## Uitbreiden

* **Nieuwe dongle-layout**: voeg een `GattLayout` toe in `core/adapters.py`.
* **Nieuw transport** (bv. Bluetooth Classic via `/dev/rfcomm0`, USB): subclass van
  `Transport` met `connect/disconnect/write` en registreer in `coordinator._build_transport`.
* **Nieuw merk/model**: JSON-profiel, zie [VEHICLE_PROFILES.md](VEHICLE_PROFILES.md).
