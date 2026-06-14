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

# Post-reset adapter config: echo off, ISO 15765-4 CAN, auto-format, long msgs,
# headers off, default receive filter. The last two explicitly undo any leftover
# monitor / open-filter state from a crashed probe.
ADAPTER_CONFIG = ("ATE0", "ATSP6", "ATCAF1", "ATAL", "ATH0", "ATCRA")

# Hard ceiling on a single open attempt. CoreBluetooth's connect and the GATT
# notify-subscribe have no reliable timeout of their own, so a wedged BLE stack
# (a half-open link left by a force-quit, or an adapter still streaming from an
# interrupted monitor) could otherwise hang the UI on "connecting…" forever.
# Must exceed a healthy connect: BLE link ~5s + reset/config ~23s worst case
# (2×1.5 spaces + 2×4 ATZ + 6×2 config), so 35s leaves headroom.
CONNECT_TIMEOUT = 35.0

T = TypeVar("T")


def _load_saved_address() -> Optional[str]:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8")).get("device_address")
    except (OSError, json.JSONDecodeError):
        return None


def _save_device(address: str, name: Optional[str]) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps({"device_address": address, "device_name": name}, indent=2), encoding="utf-8")
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
        # When auto-connect can't identify the dongle, it leaves the scanned
        # devices here and sets needs_pick so the UI can offer a device picker.
        self.candidates: list[dict] = []
        self.needs_pick: bool = False

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

    async def connect_to(self, address: str, name: Optional[str] = None) -> bool:
        """Connect to a specific user-chosen device (from the picker) by address,
        and remember it so future launches reconnect to it automatically."""
        async with self._lock:
            if self.connected:
                return True
            await self._teardown()
            self.needs_pick = False
            if not address:
                return False
            self.log(f"Connecting to selected device ({address})…")
            device = await BleakScanner.find_device_by_address(address, timeout=10.0)
            if not device:
                self.log("That device isn't in range anymore — make sure it's powered, then Rescan.")
                return False
            return await self._attempt_open(device)

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
        self.needs_pick = False

        # 1. Saved dongle fast path — connect straight to the remembered address.
        saved = self.device_address or _load_saved_address()
        if saved:
            self.log(f"Connecting to saved dongle ({saved})...")
            device = await BleakScanner.find_device_by_address(saved, timeout=10.0)
            if device and await self._attempt_open(device):
                return True
            self.log("Saved dongle not reachable — scanning...")

        # 2. Scan, and auto-connect ONLY if exactly one device clearly looks like
        #    the dongle. We never blindly grab an arbitrary device: on Windows the
        #    advertised name is often missing, and connecting to a random nearby
        #    BLE device is what made the app look like it "wouldn't connect".
        pairs = await core.discover(self.log, quiet=True)
        match = core.best_guess(pairs)
        if match and await self._attempt_open(match):
            return True

        # 3. Couldn't identify it — hand the scanned list to the UI so the user can
        #    pick their adapter once (the choice is then remembered).
        self.candidates = core.candidate_list(pairs)
        self.needs_pick = True
        if self.candidates:
            self.log(f"Couldn't auto-identify the dongle among {len(self.candidates)} "
                     "Bluetooth device(s) — pick it from the list.")
        else:
            self.log("No Bluetooth devices found. Is the dongle powered (car awake) "
                     "and is Bluetooth turned on?")
        return False

    async def _attempt_open(self, device) -> bool:
        """One bounded connect+configure attempt, with a single fresh-handle retry.

        Returns True on success (and remembers the device); logs and tears down on
        failure. Open runs under a hard ceiling so a wedged BLE stack can never
        hang on "connecting…". The retry uses a *fresh* device handle and a brief
        settle: on macOS a timed-out attempt can leave the CoreBluetooth peripheral
        mid-connect, so reusing the same handle would just re-hit it."""
        for attempt in (1, 2):
            try:
                await asyncio.wait_for(self._open(device), timeout=CONNECT_TIMEOUT)
                _save_device(device.address, device.name)
                return True
            except asyncio.CancelledError:  # Stop pressed mid-connect
                await self._teardown()
                raise
            except asyncio.TimeoutError:
                await self._teardown()
                if attempt == 1:
                    self.log(f"Connect timed out after {CONNECT_TIMEOUT:.0f}s — letting the "
                             "adapter settle, then retrying once…")
                    await asyncio.sleep(1.5)  # let the BLE stack release the stuck peripheral
                    fresh = await BleakScanner.find_device_by_address(device.address, timeout=10.0)
                    if fresh:
                        device = fresh
                    continue
                self.log("The dongle didn't finish the handshake. Power-cycle it "
                         "(unplug/replug from the OBD2 port) and try again.")
                return False
            except Exception as exc:  # surface any connect failure to the UI
                self.log(f"Connect failed: {exc}")
                await self._teardown()
                return False
        return False

    async def _open(self, device) -> None:
        client = BleakClient(device, disconnected_callback=self._on_disconnect)
        try:
            self.log("Opening BLE link to the dongle…")
            await client.connect()
            write_char, notify_char = core.find_uart_pair(client)
            if not write_char or not notify_char:
                raise RuntimeError("no write+notify characteristic pair on the dongle")
            session = ElmSession(client, write_char, notify_char)
            self.log("Subscribing to adapter notifications…")
            await session.__aenter__()
            self.log("Setting up adapter (halt any stuck stream, reset, ISO 15765-4 CAN, auto-format)...")
            await self._reset_and_configure(session)
            self._client = client
            self._session = session
            self.device_name = device.name
            self.device_address = device.address
            self.log(f"Connected to {device.name or '(unnamed)'} ({device.address}).")
        except BaseException:  # failure, timeout-cancel, or Stop — never leak a half-open BLE link
            try:
                # Bound the cleanup too: a hung disconnect would otherwise stall
                # the timeout-cancel that sent us here. Swallow *everything* here
                # (incl. a second CancelledError) so the original exception — the
                # one _ensure_locked branches on — is always what propagates.
                await asyncio.wait_for(client.disconnect(), timeout=5.0)
            except BaseException:
                pass
            raise

    async def _reset_and_configure(self, session: ElmSession) -> None:
        """Bring the adapter to a known state, recovering even if a prior crash
        left it mid-monitor (streaming) or with an open filter — that state can
        swallow a plain ATZ, so we stop the stream first and retry the reset."""
        # 1. A lone character stops a running monitor (ATMA); do it before ATZ so
        #    the reset isn't lost in a flood of streamed frames.
        for _ in range(2):
            try:
                await session.send(" ", timeout=1.5)
            except Exception:
                pass
        # 2. Reset — retry, since the first ATZ can be contaminated by the stream.
        for _ in range(2):
            try:
                reply = await session.send("ATZ", timeout=4.0)
            except Exception:
                reply = ""
            self.log(f"  {'ATZ':10s} -> {reply!r}")
            if "ELM" in reply.upper():
                break
        # 3. Configure (incl. headers off + filter reset to undo leftover state).
        #    A healthy adapter answers each in well under a second; the short
        #    timeout keeps the whole config phase inside CONNECT_TIMEOUT.
        for cmd in ADAPTER_CONFIG:
            try:
                reply = await session.send(cmd, timeout=2.0)
            except Exception as exc:
                reply = f"(error: {exc})"
            self.log(f"  {cmd:10s} -> {reply!r}")

    def _on_disconnect(self, _client) -> None:
        # bleak calls this from its loop when the BLE link drops. Clear state;
        # the next run() lazily reconnects.
        self._session = None
        self._client = None
        self.log("BLE link dropped — will reconnect on next action.")

    async def _teardown(self) -> None:
        # Clear state unconditionally — even if an await below is interrupted by a
        # (second) cancel, we must not leave a stale _client/_session that would
        # make `connected` lie on the next attempt.
        try:
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
        finally:
            self._session = None
            self._client = None
