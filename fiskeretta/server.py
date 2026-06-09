"""
Local web UI for fiskeretta.

Runs a tiny aiohttp server (same asyncio loop bleak uses) that serves a single
page and drives a *persistent* BLE connection via a shared ConnectionManager:
the dongle is connected once (auto, on launch), remembered, and reused for every
action — no rescan, no reconnect per click.

Usage:
    python3 -m fiskeretta.server      # then open http://localhost:8765
    python3 -m fiskeretta.app         # native window (no browser)
"""

import asyncio
import json
import sys
from pathlib import Path

from aiohttp import web, WSMsgType

from . import report, storage, uds
from .connection import ConnectionManager

def _resolve_static_dir() -> Path:
    """Locate the static/ dir whether running from source or a PyInstaller
    bundle (onefile, onedir, or a macOS .app where data lives in Resources)."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidates = [
            base / "fiskeretta" / "static",
            base.parent / "Resources" / "fiskeretta" / "static",  # macOS .app split
        ]
        for c in candidates:
            if (c / "index.html").is_file():
                return c
        return candidates[0]
    return Path(__file__).parent / "static"


STATIC_DIR = _resolve_static_dir()

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
            payload = json.loads(msg.data)
        except (json.JSONDecodeError, AttributeError):
            continue
        await _handle(payload, ws, log, send_status)

    return ws


async def _auto_connect(send_status) -> None:
    """Connect on launch (first client attach) if not already connected."""
    if not _manager.connected:
        await _manager.connect()
    await send_status()


async def _handle(payload: dict, ws: web.WebSocketResponse, log, send_status) -> None:
    command = payload.get("command")
    if command == "code_detail":
        await run_code_detail(ws, payload.get("module"), payload.get("code"))
        return

    actions = {
        "connect": lambda: _manager.connect(),
        "disconnect": lambda: _manager.disconnect(),
        "read_vin": lambda: run_read_vin(log),
        "read_dtcs": lambda: run_read_dtcs(log),
        "drill_failing": lambda: _manager.run(lambda s: uds.drill_failing_dtcs(s, log)),
        "scan": lambda: run_scan(ws, log),
        "saved_scans": lambda: send_saved_scans(ws),
        "discover": lambda: run_discover(ws, log),
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


async def run_scan(ws, log) -> None:
    """Full structured scan: VIN + decode + all-module DTCs -> scan_result, saved to disk."""
    result = await _manager.run(lambda s: report.build_scan_result(s, log))
    path = storage.save_scan(result)
    if not ws.closed:
        await ws.send_json({"type": "scan_result", "result": result})
    if path:
        log(f"Saved scan to {path}")


async def send_saved_scans(ws) -> None:
    if not ws.closed:
        await ws.send_json({"type": "saved_scans", "scans": storage.list_scans()})


async def run_discover(ws, log) -> None:
    """Probe the bus for responding ECUs and report the findings."""
    ecus = await _manager.run(lambda s: uds.discover_ecus(s, log))
    path = storage.save_discovery(ecus)
    if path:
        log(f"Saved discovery to {path}")
    if not ws.closed:
        await ws.send_json({"type": "discovery", "ecus": ecus})


async def run_code_detail(ws, module, code_str) -> None:
    """On-demand freeze-frame read for one code — when/where it occurred."""
    detail = {"available": False}
    try:
        detail = await _manager.run(lambda s: uds.read_code_detail(s, module, int(code_str, 16)))
    except Exception as exc:
        detail = {"available": False, "error": str(exc)}
    if not ws.closed:
        await ws.send_json({"type": "code_detail", "module": module, "code": code_str, **detail})


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
