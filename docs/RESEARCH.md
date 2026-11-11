# Fase 1 – Technisch onderzoek

Dit document legt vast wat er onderzocht is, wat de bronnen zijn en hoe zeker de
conclusies zijn. Alles wat hier als *niet geverifieerd* staat, moet nog op echte
hardware bevestigd worden (zie [Validatie op echte hardware](#validatie-op-echte-hardware)).

## 1. Vgate iCar Pro 2S

| Onderwerp | Bevinding | Zekerheid | Bron |
|---|---|---|---|
| Radio | Dual-mode: Bluetooth Classic (SPP, naam `Android-Vlink`, PIN 1234) **en** BLE (naam `IOS-Vlink`). Vendor noemt Bluetooth 5.2 | gedocumenteerd | vgatemall.com (iCar Pro 2S productpagina), obdii365 handleiding |
| Chip | Meldt `ELM327 v2.3` op `ATZ`/`ATI`; geen STN-chip (antwoordt `?` op ST-commando's) | anekdotisch | HA-community thread *Nissan Leaf via LeLink/ELM327 BLE* |
| BLE-module | Feasycom FSC-BT826N (GATT-dump van een iCar Pro BLE) | anekdotisch | Stack Overflow-vraag over IOS-Vlink |
| Seriële GATT-service A | `e7810a71-73ae-499d-8c15-faa9aef0c3f2`, één characteristic `bef8d6c9-9c21-4c9e-b632-bd58c1009f9f` (notify/indicate + write/write-without-response) | gedocumenteerd (GATT-dump + blog + OpenCarMind) | Stack Overflow, afshari.lu, github.com/AlveeeRahman/OpenCarMind |
| Seriële GATT-service B | `000018f0-…` met `00002af0` (notify) en `00002af1` (write) | **geverifieerd in broncode** (evDash: *"Default UUID's for VGate iCar Pro BLE4 adapter"*; ESPHome `ble_elm327`) + HA-gebruiker met iCar Pro 2S | github.com/nickn17/evDash `src/BoardInterface.cpp` |
| Regelafsluiting | Commando's eindigen met `\r`, antwoord compleet bij prompt `>` | geverifieerd in code (evDash) | evDash `CommObd2Ble4.cpp` |
| Pairing/bonding | BLE-kant werkt volgens vendor zonder pairing; evDash bondt optioneel | niet geverifieerd voor 2S | — |
| MTU | Geen Vgate-specifieke waarde; ≤ 20 byte per write is veilig | generiek | — |
| Slaapstand | Automatische slaap (~3 mA) en wake-up; drempel/timing onbekend; of hij in slaap blijft adverteren is onbekend | gedocumenteerd / niet geverifieerd | vgatemall.com |
| ESPHome-proxy | Werkt met actieve verbindingen via bleak, maar gebruikers melden af en toe herstarts van proxy/integratie nodig | anekdotisch | HA-community (zelfde thread) |

**Ontwerpbeslissingen die hieruit volgen**

* Geen hard-coded aanname van één UUID-paar: de BLE-transport probeert in volgorde
  E7810A71 → 18F0 → FFF0 → FFE0 en valt daarna terug op een heuristiek (vendor-service
  met notify + write). Antwoordt de adapter niet op `ATZ`/`ATI` via de eerste layout,
  dan wordt automatisch de volgende geprobeerd. De werkende layout wordt onthouden.
* Alleen de BLE-kant van de dongle wordt gebruikt. Bluetooth Classic (SPP/RFCOMM) wordt
  door Home Assistant en ESPHome-proxies **niet** ondersteund (zie §3).
* Ontdekking op naam (`IOS-Vlink*`, `vLinker*`) en op service-UUID E7810A71. Op FFF0/FFE0
  wordt niet automatisch ontdekt: die UUID's worden door talloze niet-OBD-apparaten gebruikt.
* Het MAC-adres is de unieke ID; bij vLinker-modellen wordt melding gemaakt van wisselende
  MAC-adressen (dan opnieuw toevoegen).

## 2. Home Assistant Bluetooth-architectuur

* Integraties gebruiken `homeassistant.components.bluetooth`: `async_ble_device_from_address`
  levert een `BLEDevice` dat automatisch via de beste route loopt (lokale adapter **of**
  ESPHome Bluetooth-proxy met een vrij verbindingsslot).
* Verbinden gebeurt met `bleak-retry-connector.establish_connection` +
  `BleakClientWithServiceCache` (aanbevolen door HA; zit al in HA, geen extra requirements).
* Discovery via `bluetooth`-matchers in `manifest.json` → `async_step_bluetooth`.
* Aanwezigheid: `async_register_callback` (advertenties) en `async_track_unavailable`.
  Een verbonden BLE-peripheral stopt met adverteren; de integratie negeert daarom
  "unavailable" zolang er een verbinding is.
* **Beperkingen**: ESPHome-proxies hebben standaard 3 actieve verbindingsslots
  (instelbaar); elke actieve dongle-verbinding gebruikt er één. In snapshot- en slimme
  modus wordt de verbinding na elke uitleessessie verbroken, zodat slots vrijkomen.
  Proxies moeten `active: true` hebben.

## 3. Ondersteunde verbindingsmethoden

| Methode | Status |
|---|---|
| BLE via lokale adapter | ✅ geïmplementeerd |
| BLE via ESPHome Bluetooth-proxy | ✅ geïmplementeerd (via HA-bluetooth-stack) |
| Wi-Fi ELM327 (TCP, bv. 192.168.0.10:35000) | ✅ geïmplementeerd |
| Simulator (testen zonder auto) | ✅ geïmplementeerd |
| Bluetooth Classic (SPP/RFCOMM) | ❌ niet mogelijk via HA-bluetooth/proxies; architectuur laat een `Transport`-subclass toe voor een lokaal `/dev/rfcomm`-apparaat |
| USB-serieel | ❌ niet geïmplementeerd (zelfde `Transport`-interface) |

## 4. OBD-protocol

* ELM327 met headers aan (`ATH1`) en spaties uit (`ATS0`) zodat antwoorden van meerdere
  ECU's uit elkaar gehouden worden en ISO-TP (ISO 15765-2) zelf wordt samengevoegd.
* Automatische protocoldetectie (`ATSP0` + `0100`), daarna wordt het gevonden protocol
  vastgezet (`ATSPn`) om trage nieuwe zoekacties bij een slapende bus te vermijden.
* SAE J1979 Mode 01/02/03/07/09/0A en voor profielen UDS `0x22` en KWP `0x21` (alleen lezen).
* Een whitelist in de ELM327-driver blokkeert elke andere service (o.a. 0x04 wissen
  foutcodes, 0x10 sessiewissel, 0x11 reset, 0x27 security, 0x2E/0x2F/0x31 schrijven/
  aansturen) en riskante AT-commando's (`ATPP` EEPROM, `ATMA` monitor, `ATBD`, …).

## 5. Merkspecifieke gegevens – bronnen

Alle profielformules komen uit open-sourceprojecten en zijn waar mogelijk gekruist:

* **WiCAN** (meatpiHQ/wican-fw, map `vehicle_profiles`) – formules worden letterlijk
  overgenomen; de byte-indexering (B0 = ISO-TP PCI-byte, PCI-byte elke 8 bytes) is in de
  firmware (`autopid.c`) nagekeken en wordt exact gereconstrueerd.
* **evDash** (nickn17/evDash) – tweede bron voor Hyundai/Kia, VW MEB, Peugeot.
* **CanZE** (fesch/CanZE) – Renault Zoe.

Waarden waar bronnen elkaar tegenspreken zijn **weggelaten** (o.a. Ioniq 28 kWh-kilometerstand,
VW MEB-accustroom, Leaf SOH, Zoe geladen energie). Voor BMW, Mercedes-Benz, Ford, Tesla en
Toyota/Lexus (behalve standaard-PID 0x5B) is geen betrouwbare, met een ELM327 bruikbare bron
gevonden; die merken werken met generieke OBD2.

## Validatie op echte hardware

Gebruik `scripts/ble_probe.py` (zie README) en controleer:

1. Welke GATT-layout actief wordt (verwacht: E7810A71 of 18F0).
2. Of `ATZ` "ELM327 v2.3" teruggeeft.
3. Of de dongle bij uitgeschakelde auto blijft adverteren en na hoeveel tijd hij slaapt.
4. Of verbinden via een ESPHome-proxy stabiel is.
