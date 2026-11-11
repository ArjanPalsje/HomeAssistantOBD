"""Hardware validation tool for BLE OBD2 adapters (e.g. Vgate iCar Pro 2S).

Runs outside Home Assistant on any PC / Raspberry Pi with Bluetooth:

    pip install bleak bleak-retry-connector
    python scripts/ble_probe.py                 # scan and list OBD adapters
    python scripts/ble_probe.py AA:BB:CC:DD:EE:FF [--vehicle]

It prints the GATT table, shows which serial layout the integration selects,
initialises the ELM327 and (with --vehicle) reads protocol, supported PIDs and
a few values. The VIN is never printed. Only read-only commands are sent.
Please attach the output when reporting adapter problems.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "custom_components" / "obd2_vehicle"))

from bleak import BleakScanner  # noqa: E402

from core.adapters import identify_adapter, is_known_service  # noqa: E402
from core.elm327 import ELM327  # noqa: E402
from core.exceptions import AdapterTimeout, OBDError  # noqa: E402
from core.profiles import ProfileRegistry  # noqa: E402
from core.reader import VehicleReader  # noqa: E402
from core.transport.ble import BleTransport  # noqa: E402


async def scan() -> None:
    print("Scanning 10 s …")
    found = await BleakScanner.discover(timeout=10, return_adv=True)
    for device, adv in found.values():
        model = identify_adapter(adv.local_name)
        known = any(is_known_service(u) for u in adv.service_uuids)
        if model or known:
            print(f"★ {device.address}  {adv.local_name!r}  rssi={adv.rssi}  model={model.model if model else '?'}")
    print("Done. Run again with the address to probe the adapter.")


async def probe(address: str, vehicle: bool) -> None:
    device = await BleakScanner.find_device_by_address(address, timeout=15)
    if device is None:
        print("Adapter not found – is it awake (ignition on)?")
        return
    transport = BleTransport(lambda: device, device.name or address)
    await transport.connect(30)
    diag = transport.diagnostics()
    print("GATT services:")
    for svc in diag["gatt_services"]:
        print(f"  {svc['service']}")
        for char in svc["characteristics"]:
            print(f"     {char['uuid']}  {','.join(char['properties'])}")
    print("Candidate layouts:", diag["candidate_layouts"])
    elm = ELM327(transport, command_timeout=5, log_traffic=True)
    try:
        while True:
            try:
                info = await elm.initialize()
                break
            except AdapterTimeout:
                if not await transport.next_layout():
                    raise
                print("No answer – trying next layout")
        print("Active layout:", transport.active_layout)
        print("Adapter:", info.identity, "/", info.description)
        print("Voltage:", await elm.read_adapter_voltage(), "V")
        if vehicle:
            registry = ProfileRegistry()
            registry.load_directory()
            reader = VehicleReader(elm, registry)
            await reader.start()
            print("Protocol:", elm.info.protocol_name)
            print("VIN read:", "yes" if reader.identity.vin else "no", "(not shown)")
            print("Profile:", reader.profile.name)
            print("Supported PIDs:", " ".join(f"{p:02X}" for p in sorted(reader.identity.supported_pids)))
            values = await reader.read_values(full=True)
            for key, value in sorted(values.items()):
                print(f"  {key:28} {value}")
            for key, reason in sorted(reader.unavailable.items()):
                print(f"  (unavailable) {key}: {reason}")
    except OBDError as err:
        print("Error:", err)
    finally:
        print("Recent traffic:")
        for line in elm.stats.last_commands:
            print("  ", line)
        await transport.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("address", nargs="?")
    parser.add_argument("--vehicle", action="store_true", help="also talk to the vehicle (ignition on)")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    asyncio.run(probe(args.address, args.vehicle) if args.address else scan())


if __name__ == "__main__":
    main()
