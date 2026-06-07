"""
Reusable BLE/ELM327 logic, shared by the CLI (scan.py) and the web UI (server.py).

Every function takes a `log` callback so callers can route progress messages
to a terminal, a websocket, or wherever — the BLE logic itself doesn't care.
"""

import asyncio
from typing import Callable, Optional

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice

NAME_HINTS = ("vlink", "obd", "elm")

# ATZ      - reset the adapter
# ATE0     - turn off command echo (keeps responses easy to parse)
# ATSP6    - force protocol 6: ISO 15765-4 CAN (11 bit ID, 500 kbaud) - the
#            Ocean's diagnostic bus
# ATCAF1   - turn CAN auto-formatting on (adapter handles ISO-TP framing)
HANDSHAKE = ["ATZ", "ATE0", "ATSP6", "ATCAF1", "ATI"]

PROMPT = b">"

Log = Callable[[str], None]


async def discover(log: Log, timeout: float = 10.0) -> list[BLEDevice]:
    log(f"Scanning for BLE devices ({timeout:.0f}s) — make sure the vLinker is plugged into the car's OBD2 port...")
    devices = await BleakScanner.discover(timeout=timeout)
    if not devices:
        log("No BLE devices found. Is the dongle plugged in and the ignition on (Key On, Engine Off)?")
        return []

    log(f"Found {len(devices)} device(s):")
    for d in devices:
        flag = " <-- looks like the vLinker" if _looks_like_vlinker(d) else ""
        log(f"  {d.name or '(unnamed)'}  {d.address}{flag}")
    return devices


def _looks_like_vlinker(d: BLEDevice) -> bool:
    return bool(d.name) and any(h in d.name.lower() for h in NAME_HINTS)


def best_guess(devices: list[BLEDevice]) -> Optional[BLEDevice]:
    hinted = [d for d in devices if _looks_like_vlinker(d)]
    if len(hinted) == 1:
        return hinted[0]
    return None


def find_uart_pair(client: BleakClient):
    """Heuristic: a writable characteristic + a notifiable one in the same service.

    Most ELM327 BLE clones expose a UART-bridge pattern (Nordic UART Service
    6E400001.../HM-10 FFE0/FFE1): write commands to one characteristic, get
    notified with the response on another (sometimes the same one).
    """
    for service in client.services:
        chars = service.characteristics
        write_char = next((c for c in chars if "write" in c.properties or "write-without-response" in c.properties), None)
        notify_char = next((c for c in chars if "notify" in c.properties or "indicate" in c.properties), None)
        if write_char and notify_char:
            return write_char, notify_char
    return None, None


def describe_services(client: BleakClient) -> list[str]:
    lines = []
    for service in client.services:
        lines.append(f"Service {service.uuid}  ({service.description})")
        for char in service.characteristics:
            props = ",".join(char.properties)
            lines.append(f"  Characteristic {char.uuid}  [{props}]  handle={char.handle}")
    return lines


async def run_handshake(
    client: BleakClient,
    write_char: BleakGATTCharacteristic,
    notify_char: BleakGATTCharacteristic,
    log: Log,
) -> None:
    buf = bytearray()
    response_ready = asyncio.Event()

    def on_notify(_: BleakGATTCharacteristic, data: bytearray) -> None:
        buf.extend(data)
        if PROMPT in buf:
            response_ready.set()

    await client.start_notify(notify_char, on_notify)
    log(f"Subscribed to notifications on {notify_char.uuid}. Running AT handshake via {write_char.uuid}...")

    for cmd in HANDSHAKE:
        buf.clear()
        response_ready.clear()
        payload = (cmd + "\r").encode("ascii")
        write_with_response = "write" in write_char.properties
        await client.write_gatt_char(write_char, payload, response=write_with_response)

        try:
            await asyncio.wait_for(response_ready.wait(), timeout=5.0)
            reply = bytes(buf).decode("ascii", errors="replace").strip().strip(">").strip()
            log(f"  {cmd:10s} -> {reply!r}")
        except asyncio.TimeoutError:
            log(f"  {cmd:10s} -> (no response within 5s; raw buffer: {bytes(buf)!r})")

    await client.stop_notify(notify_char)


async def connect_and_handshake(device: BLEDevice, log: Log) -> None:
    log(f"Connecting to {device.name} ({device.address})...")
    async with BleakClient(device) as client:
        log("Connected. GATT table:")
        for line in describe_services(client):
            log("  " + line)

        write_char, notify_char = find_uart_pair(client)
        if not write_char or not notify_char:
            log("Couldn't auto-detect a write+notify characteristic pair — see GATT table above.")
            return

        log(f"Guessed UART pair — write: {write_char.uuid}  notify: {notify_char.uuid}")
        await run_handshake(client, write_char, notify_char, log)
