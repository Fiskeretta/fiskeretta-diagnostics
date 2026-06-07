"""
Simple local web UI for ocean-diag.

Runs a tiny aiohttp server (same asyncio loop bleak uses — no thread juggling
between BLE and the GUI) that serves a single page with a "Scan & Connect"
button and a live log fed over a websocket.

Usage:
    python3 -m ocean_diag.server
    -> open http://localhost:8765 in a browser
"""

import asyncio
import json
from pathlib import Path

from aiohttp import web, WSMsgType

from . import core, uds

STATIC_DIR = Path(__file__).parent / "static"

# Only one BLE operation at a time — the dongle can't serve two clients.
_busy_lock = asyncio.Lock()


async def index(request: web.Request) -> web.Response:
    return web.FileResponse(STATIC_DIR / "index.html")


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    def log(message: str) -> None:
        asyncio.create_task(ws.send_json({"type": "log", "message": message}))

    async for msg in ws:
        if msg.type != WSMsgType.TEXT:
            continue
        try:
            command = json.loads(msg.data).get("command")
        except (json.JSONDecodeError, AttributeError):
            continue

        if command in ("scan_and_connect", "read_vin"):
            if _busy_lock.locked():
                await ws.send_json({"type": "log", "message": "Already running a BLE operation — wait for it to finish."})
                continue
            async with _busy_lock:
                await ws.send_json({"type": "status", "value": "running"})
                try:
                    if command == "scan_and_connect":
                        await run_scan_and_connect(log)
                    else:
                        await run_read_vin(log)
                finally:
                    await ws.send_json({"type": "status", "value": "idle"})

    return ws


async def find_vlinker(log: core.Log):
    devices = await core.discover(log)
    if not devices:
        return None

    device = core.best_guess(devices) or (devices[0] if devices else None)
    if device and not core.best_guess(devices):
        log(f"No clear vLinker match — trying the first device found: {device.name or '(unnamed)'} ({device.address})")
    return device


async def run_scan_and_connect(log: core.Log) -> None:
    device = await find_vlinker(log)
    if device:
        await core.connect_and_handshake(device, log)


async def run_read_vin(log: core.Log) -> None:
    device = await find_vlinker(log)
    if device:
        await uds.read_vin_from_device(device, log)


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_static("/static/", STATIC_DIR)
    return app


def main() -> None:
    web.run_app(build_app(), host="localhost", port=8765)


if __name__ == "__main__":
    main()
