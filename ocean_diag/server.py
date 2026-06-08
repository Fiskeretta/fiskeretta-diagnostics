"""
Local web UI for ocean-diag.

Runs a tiny aiohttp server (same asyncio loop bleak uses) that serves a single
page and drives a *persistent* BLE connection via a shared ConnectionManager:
the dongle is connected once (auto, on launch), remembered, and reused for every
action — no rescan, no reconnect per click.

Usage:
    python3 -m ocean_diag.server      # then open http://localhost:8765
    python3 -m ocean_diag.app         # native window (no browser)
"""

import asyncio
import json
from pathlib import Path

from aiohttp import web, WSMsgType

from . import uds
from .connection import ConnectionManager

STATIC_DIR = Path(__file__).parent / "static"

# One shared connection for the whole process — survives page reloads / ws
# reconnects, so the BLE link stays up across them.
_manager = ConnectionManager()


async def index(request: web.Request) -> web.Response:
    return web.FileResponse(STATIC_DIR / "index.html")


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    def log(message: str) -> None:
        if not ws.closed:
            asyncio.create_task(ws.send_json({"type": "log", "message": message}))

    async def send_status() -> None:
        if not ws.closed:
            await ws.send_json({"type": "connection", **_manager.status()})

    # Route the shared manager's progress to whichever client is attached now.
    _manager.log = log
    await send_status()
    asyncio.create_task(_auto_connect(send_status))

    async for msg in ws:
        if msg.type != WSMsgType.TEXT:
            continue
        try:
            command = json.loads(msg.data).get("command")
        except (json.JSONDecodeError, AttributeError):
            continue
        await _handle(command, ws, log, send_status)

    return ws


async def _auto_connect(send_status) -> None:
    """Connect on launch (first client attach) if not already connected."""
    if not _manager.connected:
        await _manager.connect()
    await send_status()


async def _handle(command: str, ws: web.WebSocketResponse, log, send_status) -> None:
    actions = {
        "connect": lambda: _manager.connect(),
        "disconnect": lambda: _manager.disconnect(),
        "read_vin": lambda: run_read_vin(log),
        "read_dtcs": lambda: run_read_dtcs(log),
        "drill_failing": lambda: _manager.run(lambda s: uds.drill_failing_dtcs(s, log)),
    }
    if command not in actions:
        return

    await ws.send_json({"type": "status", "value": "running"})
    try:
        await actions[command]()
    except Exception as exc:  # surface failures to the UI instead of dying
        log(f"Error: {exc}")
    finally:
        await ws.send_json({"type": "status", "value": "idle"})
        await send_status()


async def run_read_vin(log) -> None:
    log("Reading VIN from the Gateway module (UDS 0x22, DID 0xF190)...")
    vin = await _manager.run(lambda s: uds.read_vin(s, log))
    log(f"VIN: {vin}")


async def run_read_dtcs(log) -> None:
    report = await _manager.run(lambda s: uds.read_all_dtcs(s, log))
    all_dtcs = [dtc for dtcs in report.values() if dtcs for dtc in dtcs]
    failing_now = [d for d in all_dtcs if d.is_failing_now]
    confirmed = [d for d in all_dtcs if d.is_confirmed]
    log("")
    if not all_dtcs:
        log("No DTCs found on any queried module.")
    elif not confirmed:
        log(f"{len(all_dtcs)} DTC table entries seen, none confirmed — nothing to worry about.")
    else:
        log(f"{len(failing_now)} failing right now, {len(confirmed)} confirmed (may be historical), out of {len(all_dtcs)} table entries.")
        log("'FAILING NOW' is the actionable list — 'confirmed' codes persist until cleared even if the underlying issue resolved.")


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
