# OBD2 Vehicle voor Home Assistant

Lees je auto automatisch uit via een Bluetooth- of Wi-Fi-OBD2-dongle zodra hij thuiskomt.
Werkt voor benzine, diesel, hybride, plug-in-hybride en elektrische auto's. Primair ontworpen
voor de **Vgate iCar Pro 2S**.

* Automatische ontdekking van de dongle (lokale Bluetooth én ESPHome Bluetooth-proxies)
* Automatisch verbinden, uitlezen en weer loskoppelen; herverbinden met exponentiële backoff
* Identificatie via VIN, automatische keuze van het voertuigprofiel
* Generieke OBD2 (SAE J1979) + merkspecifieke profielen (Hyundai/Kia, VW-groep MEB, Renault Zoe, Nissan Leaf, Peugeot e-208)
* Snapshot-, live- en slimme uitleesmodus
* Laatst bekende waarden blijven bewaard (ook na herstart), realtimewaarden worden nooit als actueel getoond als ze dat niet zijn
* **Meerdere dongles, elk bij een eigen auto**, tegelijk
* Strikt **alleen lezen**: wissen van foutcodes, sessiewissels, schrijven en aansturen zijn in de driver geblokkeerd
* Volledig via de UI in te stellen, Nederlands en Engels

> **Status:** de software is volledig getest tegen een ELM327/voertuigsimulator (zie [Testen](#testen)).
> Validatie op een echte Vgate iCar Pro 2S en op echte voertuigen moet nog gebeuren – zie
> [Wat is getest en wat niet](#wat-is-getest-en-wat-niet).

---

## Installatie

### Via HACS (custom repository)

1. HACS → menu ⋮ → **Custom repositories** → URL van deze repository, type **Integration**.
2. Zoek **OBD2 Vehicle** en installeer.
3. Herstart Home Assistant.

### Handmatig

Kopieer `custom_components/obd2_vehicle` naar `<config>/custom_components/` en herstart.

Vereisten: Home Assistant 2025.2 of nieuwer, de **Bluetooth**-integratie (lokale adapter of
ESPHome-proxy met `bluetooth_proxy: active: true`) voor BLE-dongles.

## Instellen

1. Stop de dongle in de OBD-poort en zet het contact aan (de dongle wordt dan wakker).
2. Home Assistant toont meestal vanzelf **"OBD2-dongle gevonden"** (de iCar Pro 2S heet
   `IOS-Vlink`). Anders: Instellingen → Apparaten & diensten → Integratie toevoegen → **OBD2 Vehicle**
   → *Bluetooth LE-dongle*. Herkende dongles zijn gemarkeerd met ★.
3. Geef het voertuig een naam, kies het profiel (*Automatisch* is aanbevolen) en de uitleesmodus.
4. Klaar. Voor een **tweede auto met een eigen dongle** voeg je de integratie gewoon nog een keer
   toe; al gekoppelde dongles worden niet meer getoond.

> Koppel de dongle niet tegelijk met een telefoon-app: een BLE-dongle accepteert maar één verbinding.

### Opties (Instellingen → OBD2 Vehicle → Configureren)

| Optie | Standaard | Uitleg |
|---|---|---|
| Voertuignaam | – | Naam van het apparaat |
| Uitleesmodus | Slim | zie hieronder |
| Voertuigprofiel | Automatisch | Handmatige keuze als het VIN geen uitsluitsel geeft (bv. E-GMP met laadvlaggen) |
| Live-interval | 15 s | Interval tijdens rijden / live-modus |
| Interval tijdens laden | 300 s | Slimme modus, voertuig laadt |
| Interval geparkeerd met contact aan | 1800 s | Slimme modus |
| Controle-interval slapend voertuig | 3600 s | Hoe vaak gekeken wordt of een slapende auto wakker is. **0 = alleen bij aankomst/handmatig** |
| Interval volledige uitlezing | 3600 s | Trage waarden (km-stand, SOH) en foutcodes |
| Sensorgroepen | alle behalve emissies | Algemeen, motor, emissies, EV/hybride, merkspecifiek |
| Geavanceerde diagnostiek | uit | Permanente foutcodes (Mode 0A) en freeze-frame |
| Ruwe OBD-communicatie loggen | uit | Elk commando + antwoord in het debuglog (VIN wordt geredigeerd) |
| Timeout per commando | 5 s | Verhogen bij trage dongles/proxies |

### Uitleesmodi

| Modus | Gedrag |
|---|---|
| **Snapshot** | Eén volledige uitlezing kort na aankomst, daarna niets tot de volgende aankomst (met max. 3 herhaalpogingen als de auto niet antwoordt). Zuinigst. |
| **Live** | Zolang het voertuig antwoordt elke *live-interval* uitlezen, verbinding blijft open. |
| **Slim** (aanbevolen) | Snapshot na aankomst; daarna: motor draait → live-interval; laden → laadinterval; contact aan maar stil → geparkeerd-interval; voertuig antwoordt niet → verbinding dicht en alleen het controle-interval. |

"Aankomst" = de dongle verschijnt opnieuw nadat hij minstens 5 minuten weg was.

**Waarom niet vaker?** Elk diagnostisch verzoek kan regeleenheden van een geparkeerde auto wakker
houden of maken en zo de 12V-accu belasten. Daarom stopt de integratie zodra de ECU niet meer
antwoordt en wordt een slapende auto alleen met een ruim interval (of helemaal niet) gecontroleerd.

## Entiteiten

Per voertuig één apparaat. Welke sensoren verschijnen hangt af van wat de auto ondersteunt
(ondersteunde PID's worden automatisch uitgelezen); nieuwe sensoren verschijnen na de eerste
succesvolle uitlezing.

**Altijd:** verbindingsstatus, laatste succesvolle uitlezing, laatste volledige uitlezing,
laatste verbinding, 12V-accu (gemeten door dongle), opgeslagen/pending foutcodes (aantal + codes
als attribuut), VIN, OBD-protocol, dongle, voertuigprofiel, signaalsterkte (BLE, standaard uit),
binary sensors *dongle binnen bereik*, *verbonden*, *voertuig reageert*, *motor draait*,
*foutcodes aanwezig*, knoppen *nu volledig uitlezen*, *opnieuw verbinden*, *voertuig opnieuw identificeren*.

**Generieke OBD2 (indien ondersteund):** brandstofniveau, brandstoftype, toerental, snelheid,
koelvloeistof-, inlaatlucht-, olie- en buitentemperatuur, motorbelasting, gaspedaal/gasklep,
motorkoppel (%, Nm), brandstofdebiet, inlaatspruitstukdruk, luchtmassa, boordspanning ECU, MIL,
afstand sinds wissen codes, kilometerstand (PID A6, alleen nieuwere auto's), hybride
accupakket (PID 5B), fuel trims en meer (deels standaard uitgeschakeld).

**Merkspecifiek (profielen):** HV-accu SOC (BMS en dashboard), SOH, spanning, stroom, vermogen,
laadvermogen, temperaturen, 12V-accu, kilometerstand, laadstatus, AC/DC-laden, totaal
geladen/ontladen energie – afhankelijk van het profiel.

> **12V versus hoogspanning:** alles met *12V* (`adapter_voltage`, `control_module_voltage`,
> `aux_battery_voltage`) betreft de boordnetaccu. Alles met *HV-accu* (`hv_*`) betreft het
> hoogspanningspakket.

### Realtime of laatst bekend?

* Realtimewaarden (toerental, snelheid, stroom, vermogen, temperaturen) zijn **onbeschikbaar**
  zodra er geen actieve uitleessessie is.
* Toestandswaarden (brandstof, SOC, SOH, km-stand, foutcodes) blijven staan met het attribuut
  `data_status: live` of `last_known`. Het tijdstip staat in *Laatste succesvolle uitlezing*.

### Events voor automatiseringen

* `obd2_vehicle_read_completed` – `{entry_id, vehicle, full}`
* `obd2_vehicle_dtc_detected` – `{entry_id, vehicle, codes}` bij nieuwe opgeslagen foutcodes

Voorbeelden: [examples/automations.yaml](examples/automations.yaml), dashboard:
[examples/dashboard.yaml](examples/dashboard.yaml).

Er is bewust **geen `device_tracker`**: de aanwezigheid van de dongle is geen betrouwbare locatie
(hij slaapt na een tijd en adverteert dan niet meer, terwijl de auto gewoon thuis staat). Gebruik
*dongle binnen bereik* alleen als aanvullend signaal.

## Ondersteuning

### Dongles

| Dongle | Verbinding | Status |
|---|---|---|
| **Vgate iCar Pro 2S** (`IOS-Vlink`) | BLE | Primair doel. GATT-layouts uit broncode/GATT-dumps; automatische layoutkeuze. Nog te valideren op hardware |
| Vgate iCar Pro BLE 4.0 | BLE | Zelfde layouts, verwacht te werken |
| Vgate vLinker MC+/FS/MS (BLE-firmware) | BLE | Layout E7810A71, verwacht te werken |
| Generieke ELM327 BLE (FFF0/FFE0) | BLE | Heuristische detectie; klonen variëren sterk |
| Wi-Fi ELM327 (bv. iCar 2 Wi-Fi) | TCP | Geïmplementeerd |
| Bluetooth Classic (SPP)-dongles, incl. de Classic-kant van de 2S | – | **Niet ondersteund**: Home Assistant/ESPHome-proxies ondersteunen geen RFCOMM |
| OBDLink (STN) | BLE/Wi-Fi | ELM327-compatibel, niet getest |

### Voertuigprofielen

| Profiel | Status | Bronnen |
|---|---|---|
| Generieke OBD2 | standaard (alle OBD2-auto's, EU benzine ≥2001, diesel ≥2004) | SAE J1979 |
| Hyundai/Kia Kona Electric, e-Niro, e-Soul 64 kWh, Ioniq 5/6, EV6 | community | WiCAN + evDash |
| Hyundai/Kia E-GMP met laadvlaggen (handmatig kiezen) | community | WiCAN + evDash |
| Hyundai Ioniq Electric 28 kWh | community | WiCAN + evDash |
| VW ID.3/ID.4/ID.5, Škoda Enyaq, Audi Q4, Cupra Born (MEB) | community | WiCAN + evDash |
| Peugeot e-208 / e-2008 | community | WiCAN + evDash |
| Renault Zoe ZE50 / ZE40 | experimenteel | WiCAN + CanZE |
| Nissan Leaf AZE0 | experimenteel | WiCAN |
| BMW/MINI, Mercedes-Benz, Toyota/Lexus, Ford, Stellantis (verbranding), Tesla | generieke OBD2 | geen betrouwbare open bron voor extra data met een ELM327 |

*community* = formules kloppen tussen twee onafhankelijke open-sourceprojecten;
*experimenteel* = één bron of onzekerheid, niet op een voertuig gevalideerd. Eigen profiel
toevoegen: [docs/VEHICLE_PROFILES.md](docs/VEHICLE_PROFILES.md).

## Beperkingen

**Door de auto**
* Veel EV-waarden (SOC, SOH, laden) zijn niet via standaard-OBD2 beschikbaar, alleen via merkprofielen.
* Kilometerstand via PID A6 bestaat pas sinds ca. 2019; oudere auto's alleen via een profiel.
* Sommige auto's (o.a. met gateway/security, zoals nieuwere Stellantis/FCA "SGW", Tesla) blokkeren
  of beperken diagnose; sommige ECU's antwoorden alleen met contact aan.
* Een gesloten/slapende auto antwoordt meestal niet; de integratie toont dan de laatst bekende waarden.

**Door de dongle**
* BLE-dongles accepteren één verbinding tegelijk.
* Slaapgedrag (drempelspanning, adverteren tijdens slaap) van de iCar Pro 2S is niet gedocumenteerd.
* Goedkope klonen implementeren AT-commando's soms onvolledig.

**Door Home Assistant / Bluetooth**
* Geen Bluetooth Classic.
* ESPHome-proxies hebben een beperkt aantal gelijktijdige verbindingen (standaard 3); in snapshot-
  en slimme modus wordt de verbinding na elke sessie vrijgegeven. Live-modus houdt een slot bezet.
* BLE-bereik: de dongle zit in de auto (metaal); plaats een proxy dicht bij de garage/oprit.

## Probleemoplossing

* **Debuglog:** Instellingen → Apparaten & diensten → OBD2 Vehicle → *Debuglogboek inschakelen*.
  Zet daarnaast in de opties *Ruwe OBD-communicatie loggen* aan om elk commando te zien.
* **Diagnostiek downloaden** (⋮ bij de integratie): bevat GATT-services, actieve layout, adaptergegevens,
  laatste 30 commando's, ondersteunde PID's, redenen waarom sensoren niet beschikbaar zijn
  (`unavailable_reasons`) en de plannerstatus. VIN, MAC-adres en host worden geredigeerd.
* **Dongle buiten HA testen:**
  ```bash
  pip install bleak bleak-retry-connector
  python scripts/ble_probe.py
  python scripts/ble_probe.py AA:BB:CC:DD:EE:FF --vehicle
  ```

## Testen

```bash
pip install -r requirements_test.txt
pytest -q
```

De HA-tests (`tests/test_ha_integration.py`) vereisen `pytest-homeassistant-custom-component` en
Linux/macOS; ze draaien in GitHub Actions en worden anders automatisch overgeslagen.

De tests draaien de volledige keten (transport → ELM327 → ISO-TP → PID's → profielen → planner)
tegen een simulator die echte ELM327-uitvoer produceert (echo, headers, multi-frame, BLE-chunks van
20 bytes) inclusief foutinjectie (timeouts, corrupte en ontbrekende frames, contact uit, ECU zonder
multi-PID-ondersteuning, ander voertuig op dezelfde dongle). In Home Assistant kun je bovendien de
verbinding **Simulator** kiezen (benzine- of EV-demo) om entiteiten, dashboards en automatiseringen
zonder auto te testen.

### Wat is getest en wat niet

| Onderdeel | Getest |
|---|---|
| ELM327-driver, ISO-TP, PID-conversies, DTC/VIN, formules, profielen, planner | ✅ geautomatiseerd tegen simulator |
| Read-only-beveiliging (geblokkeerde services/AT-commando's) | ✅ geautomatiseerd |
| HA-laag (config flow, entiteiten, coordinator, diagnostiek) | ✅ geautomatiseerd in een echte HA-testinstantie (HA 2026.10, `pytest-homeassistant-custom-component`) met de simulator; het BLE-pad zelf wordt daarbij niet doorlopen |
| Vgate iCar Pro 2S over BLE (GATT-layout, slaapgedrag) | ❌ moet op hardware gevalideerd worden (`scripts/ble_probe.py`) |
| ESPHome-proxy | ❌ moet gevalideerd worden |
| Merkprofielen op echte voertuigen | ❌ formules uit open bronnen, niet zelf gevalideerd |

## Veiligheid en privacy

* De driver laat alleen de services 01, 02, 03, 07, 09, 0A, 21 en 22 door. Foutcodes wissen (04/14),
  sessiewissel (10), reset (11), security access (27), schrijven (2E), aansturen (2F/31/08) en
  EEPROM-AT-commando's zijn geblokkeerd – ook als een profiel ze zou bevatten.
* VIN-antwoorden worden niet gelogd; diagnostiek bevat alleen een niet-omkeerbare VIN-vingerafdruk.
* Verwijderen van de integratie verwijdert ook de opgeslagen gegevens.

## Documentatie

* [docs/RESEARCH.md](docs/RESEARCH.md) – technisch onderzoek (Vgate, HA-Bluetooth, bronnen)
* [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) – architectuur en sessieverloop
* [docs/VEHICLE_PROFILES.md](docs/VEHICLE_PROFILES.md) – profielen toevoegen

## Credits

Merkspecifieke formules zijn afkomstig uit [WiCAN](https://github.com/meatpiHQ/wican-fw),
[evDash](https://github.com/nickn17/evDash) en [CanZE](https://github.com/fesch/CanZE).

Licentie: MIT.
