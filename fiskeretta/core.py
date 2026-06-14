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


async def discover(log: Log, timeout: float = 10.0, quiet: bool = False) -> list[BLEDevice]:
    """Scan for BLE devices. `quiet` suppresses the chatty per-device listing —
    the app drives discovery via the connection manager and doesn't want the
    noise in its log; the CLI leaves quiet=False to show the full list."""
    if not quiet:
        log(f"Scanning for BLE devices ({timeout:.0f}s) — make sure the vLinker is plugged into the car's OBD2 port...")
    devices = await BleakScanner.discover(timeout=timeout)
    if not devices:
        if not quiet:
            log("No BLE devices found. Is the dongle plugged in and the ignition on (Key On, Engine Off)?")
        return []

    if not quiet:
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


class ElmSession:
    """A connected ELM327 session: subscribe once, send commands, read replies.

    Every command (AT or raw UDS hex) gets written with a trailing '\\r' and
    the reply is whatever arrives before the '>' prompt — that's how the
    ELM327 line discipline works over any transport (serial, BLE, etc).
    """

    # Cap the receive buffer so a runaway stream (e.g. monitoring a live bus with
    # an open filter) can't grow it without bound. Real replies are well under
    # this; only a firehose ever hits the limit, and we keep the newest tail.
    _MAX_BUF = 1 << 16  # 64 KiB

    def __init__(self, client: BleakClient, write_char: BleakGATTCharacteristic, notify_char: BleakGATTCharacteristic):
        self.client = client
        self.write_char = write_char
        self.notify_char = notify_char
        self._buf = bytearray()
        self._ready = asyncio.Event()

    async def __aenter__(self) -> "ElmSession":
        await self.client.start_notify(self.notify_char, self._on_notify)
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.client.stop_notify(self.notify_char)

    def _on_notify(self, _: BleakGATTCharacteristic, data: bytearray) -> None:
        self._buf.extend(data)
        # Only scan the freshly-arrived tail for the prompt — scanning the whole
        # (possibly huge) buffer on every packet is O(n²) under a heavy stream and
        # saturates the event loop, which is what froze the app.
        if PROMPT in self._buf[-(len(data) + 1):]:
            self._ready.set()
        # Bound memory: keep only the newest tail if a flood outruns the reader.
        if len(self._buf) > self._MAX_BUF:
            del self._buf[:-self._MAX_BUF]

    async def send(self, command: str, timeout: float = 5.0) -> str:
        """Send a command, wait for the '>' prompt, return the decoded reply
        (command echo and prompt stripped)."""
        self._buf.clear()
        self._ready.clear()
        payload = (command + "\r").encode("ascii")
        write_with_response = "write" in self.write_char.properties
        await self.client.write_gatt_char(self.write_char, payload, response=write_with_response)

        await asyncio.wait_for(self._ready.wait(), timeout=timeout)
        raw = bytes(self._buf).decode("ascii", errors="replace")
        # Strip the command echo (if ATE0 hasn't taken effect yet) and the prompt.
        reply = raw.strip()
        if reply.startswith(command):
            reply = reply[len(command):]
        return reply.strip().strip(">").strip()

    async def monitor(self, command: str, seconds: float) -> str:
        """Run a streaming monitor command (e.g. ATMA) that never returns a '>'
        prompt on its own. Send it, let notifications accumulate for `seconds`,
        snapshot what arrived, then send a single byte to stop the monitor and
        return the adapter to command mode. Returns the captured text."""
        self._buf.clear()
        self._ready.clear()
        write_with_response = "write" in self.write_char.properties
        await self.client.write_gatt_char(
            self.write_char, (command + "\r").encode("ascii"), response=write_with_response)

        await asyncio.sleep(seconds)
        captured = bytes(self._buf).decode("ascii", errors="replace")

        # Any single character stops a running monitor; the byte is otherwise
        # ignored. Then drain the prompt so the next send() starts clean.
        try:
            await self.client.write_gatt_char(self.write_char, b" ", response=write_with_response)
            await asyncio.wait_for(self._ready.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pass
        return captured


async def run_handshake(session: ElmSession, log: Log) -> None:
    log(f"Running AT handshake via {session.write_char.uuid} / {session.notify_char.uuid}...")
    for cmd in HANDSHAKE:
        try:
            reply = await session.send(cmd)
            log(f"  {cmd:10s} -> {reply!r}")
        except asyncio.TimeoutError:
            log(f"  {cmd:10s} -> (no response within 5s)")


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
        async with ElmSession(client, write_char, notify_char) as session:
            await run_handshake(session, log)
