"""
Persistent BLE/ELM327 connection manager.

Holds a single live BleakClient + ElmSession so the app connects once and
reuses it for every operation — no rescan, no reconnect per action. Remembers
the dongle's address (saved on first connect) so later launches connect
straight to it. All session use is serialized through one lock, since the
ElmSession's receive buffer isn't safe for concurrent commands.
"""

import asyncio
import json
from pathlib import Path
from typing import Awaitable, Callable, Optional, TypeVar

from bleak import BleakClient, BleakScanner

from . import core
from .core import ElmSession, Log

CONFIG_DIR = Path.home() / ".config" / "fiskeretta"
CONFIG_FILE = CONFIG_DIR / "config.json"

# One-time adapter setup: reset, echo off, ISO 15765-4 CAN, auto-format, long msgs.
ADAPTER_SETUP = ("ATZ", "ATE0", "ATSP6", "ATCAF1", "ATAL")

T = TypeVar("T")


def _load_saved_address() -> Optional[str]:
    try:
        return json.loads(CONFIG_FILE.read_text()).get("device_address")
    except (OSError, json.JSONDecodeError):
        return None


def _save_device(address: str, name: Optional[str]) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps({"device_address": address, "device_name": name}, indent=2))
    except OSError:
        pass


class ConnectionManager:
    """Owns the one live connection. `log` is a settable sink so whichever UI
    client is currently attached receives progress messages."""

    def __init__(self, log: Optional[Log] = None):
        self.log: Log = log or (lambda _msg: None)
        self._client: Optional[BleakClient] = None
        self._session: Optional[ElmSession] = None
        self._lock = asyncio.Lock()
        self.device_name: Optional[str] = None
        self.device_address: Optional[str] = None

    @property
    def connected(self) -> bool:
        return bool(self._client and self._client.is_connected and self._session)

    def status(self) -> dict:
        return {"connected": self.connected, "device": self.device_name or self.device_address}

    async def connect(self) -> bool:
        """Ensure a live connection. Idempotent; scans only if we have no saved
        device (or it isn't in range)."""
        async with self._lock:
            return await self._ensure_locked()

    async def disconnect(self) -> None:
        async with self._lock:
            await self._teardown()
            self.log("Disconnected.")

    async def run(self, fn: Callable[[ElmSession], Awaitable[T]]) -> T:
        """Run an operation against the live session, (re)connecting if needed.
        Serialized so two operations never share the session concurrently."""
        async with self._lock:
            if not await self._ensure_locked():
                raise ConnectionError("could not connect to the dongle")
            return await fn(self._session)

    # --- internals (call with _lock held) ---

    async def _ensure_locked(self) -> bool:
        if self.connected:
            return True
        await self._teardown()  # clear any half-open state

        device = await self._find_device()
        if not device:
            self.log("No dongle found. Is it plugged into the OBD2 port and the car awake?")
            return False

        try:
            await self._open(device)
        except Exception as exc:  # surface any connect failure to the UI
            self.log(f"Connect failed: {exc}")
            await self._teardown()
            return False

        _save_device(device.address, device.name)
        return True

    async def _find_device(self):
        address = self.device_address or _load_saved_address()
        if address:
            self.log(f"Connecting to saved dongle ({address})...")
            device = await BleakScanner.find_device_by_address(address, timeout=10.0)
            if device:
                return device
            self.log("Saved dongle not in range — scanning...")
        devices = await core.discover(self.log, quiet=True)
        return core.best_guess(devices) or (devices[0] if devices else None)

    async def _open(self, device) -> None:
        client = BleakClient(device, disconnected_callback=self._on_disconnect)
        await client.connect()
        write_char, notify_char = core.find_uart_pair(client)
        if not write_char or not notify_char:
            await client.disconnect()
            raise RuntimeError("no write+notify characteristic pair on the dongle")

        session = ElmSession(client, write_char, notify_char)
        await session.__aenter__()
        self.log("Setting up adapter (reset, echo off, ISO 15765-4 CAN, auto-format, long messages)...")
        for cmd in ADAPTER_SETUP:
            reply = await session.send(cmd)
            self.log(f"  {cmd:10s} -> {reply!r}")

        self._client = client
        self._session = session
        self.device_name = device.name
        self.device_address = device.address
        self.log(f"Connected to {device.name or '(unnamed)'} ({device.address}).")

    def _on_disconnect(self, _client) -> None:
        # bleak calls this from its loop when the BLE link drops. Clear state;
        # the next run() lazily reconnects.
        self._session = None
        self._client = None
        self.log("BLE link dropped — will reconnect on next action.")

    async def _teardown(self) -> None:
        if self._session is not None:
            try:
                await self._session.__aexit__(None, None, None)
            except Exception:
                pass
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:
                pass
        self._session = None
        self._client = None
