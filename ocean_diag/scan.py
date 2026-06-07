"""
Phase 0: find the vLinker FD+ over BLE, map its GATT table, and run the
ELM327 AT handshake (ATZ / ATE0 / ATSP6) over whatever write+notify
characteristic pair it exposes.

This is the de-risking step: it only needs the dongle plugged into the car's
OBD2 port with the ignition in "Key On, Engine Off" — the car doesn't need to
be in Ready/driving state.

Usage:
    python3 -m ocean_diag.scan
"""

import asyncio
import sys

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic

NAME_HINTS = ("vlink", "obd", "elm")

# AT commands to run once we have a working write/notify pair.
# ATZ      - reset the adapter
# ATE0     - turn off command echo (keeps responses easy to parse)
# ATSP6    - force protocol 6: ISO 15765-4 CAN (11 bit ID, 500 kbaud) - the
#            Ocean's diagnostic bus
# ATCAF1   - turn CAN auto-formatting on (adapter handles ISO-TP framing)
HANDSHAKE = ["ATZ", "ATE0", "ATSP6", "ATCAF1", "ATI"]

PROMPT = b">"


async def discover() -> list:
    print("Scanning for BLE devices (10s) — make sure the vLinker is plugged into the car's OBD2 port...")
    devices = await BleakScanner.discover(timeout=10.0)
    if not devices:
        print("No BLE devices found. Is the dongle plugged in and the car's ignition on (Key On, Engine Off)?")
        return []

    print(f"\nFound {len(devices)} device(s):")
    for i, d in enumerate(devices):
        flag = "  <-- name matches vLinker/OBD/ELM" if d.name and any(h in d.name.lower() for h in NAME_HINTS) else ""
        print(f"  [{i}] {d.name or '(unnamed)'}  {d.address}{flag}")
    return devices


def pick_device(devices: list):
    hinted = [d for d in devices if d.name and any(h in d.name.lower() for h in NAME_HINTS)]
    if len(hinted) == 1:
        print(f"\nAuto-selecting likely match: {hinted[0].name} ({hinted[0].address})")
        return hinted[0]

    if not sys.stdin.isatty():
        if hinted:
            return hinted[0]
        return devices[0] if devices else None

    raw = input("\nEnter the index of the vLinker device: ").strip()
    try:
        return devices[int(raw)]
    except (ValueError, IndexError):
        print("Invalid index.")
        return None


def dump_services(client: BleakClient) -> None:
    print(f"\nGATT table for {client.address}:")
    for service in client.services:
        print(f"  Service {service.uuid}  ({service.description})")
        for char in service.characteristics:
            props = ",".join(char.properties)
            print(f"    Characteristic {char.uuid}  [{props}]  handle={char.handle}")


def find_uart_pair(client: BleakClient):
    """Heuristic: look for a writable characteristic and a notifiable one.

    Most ELM327 BLE clones expose a UART-bridge pattern (e.g. Nordic UART
    Service 6E400001.../HM-10 FFE0/FFE1): one characteristic you write
    command bytes to, one that notifies you with the response bytes. They
    are sometimes the same characteristic, sometimes a pair in one service.
    """
    write_char = None
    notify_char = None
    for service in client.services:
        candidates = service.characteristics
        local_write = next((c for c in candidates if "write" in c.properties or "write-without-response" in c.properties), None)
        local_notify = next((c for c in candidates if "notify" in c.properties or "indicate" in c.properties), None)
        if local_write and local_notify and write_char is None:
            write_char, notify_char = local_write, local_notify
    return write_char, notify_char


async def run_handshake(client: BleakClient, write_char: BleakGATTCharacteristic, notify_char: BleakGATTCharacteristic) -> None:
    buf = bytearray()
    response_ready = asyncio.Event()

    def on_notify(_: BleakGATTCharacteristic, data: bytearray) -> None:
        buf.extend(data)
        if PROMPT in buf:
            response_ready.set()

    await client.start_notify(notify_char, on_notify)
    print(f"\nSubscribed to notifications on {notify_char.uuid}. Running AT handshake via {write_char.uuid}...\n")

    for cmd in HANDSHAKE:
        buf.clear()
        response_ready.clear()
        payload = (cmd + "\r").encode("ascii")
        write_with_response = "write" in write_char.properties
        await client.write_gatt_char(write_char, payload, response=write_with_response)

        try:
            await asyncio.wait_for(response_ready.wait(), timeout=5.0)
            reply = bytes(buf).decode("ascii", errors="replace").strip().strip(">").strip()
            print(f"  {cmd:10s} -> {reply!r}")
        except asyncio.TimeoutError:
            print(f"  {cmd:10s} -> (no response within 5s; raw buffer: {bytes(buf)!r})")

    await client.stop_notify(notify_char)


async def main() -> None:
    devices = await discover()
    if not devices:
        return

    device = pick_device(devices)
    if device is None:
        return

    print(f"\nConnecting to {device.name} ({device.address})...")
    async with BleakClient(device) as client:
        print("Connected.")
        dump_services(client)

        write_char, notify_char = find_uart_pair(client)
        if not write_char or not notify_char:
            print("\nCouldn't auto-detect a write+notify characteristic pair.")
            print("Re-run with the GATT table above and we'll hardcode the right UUIDs.")
            return

        print(f"\nGuessed UART pair — write: {write_char.uuid}  notify: {notify_char.uuid}")
        await run_handshake(client, write_char, notify_char)


if __name__ == "__main__":
    asyncio.run(main())
