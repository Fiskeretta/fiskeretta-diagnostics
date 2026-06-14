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

from . import dtc_catalog, modules, registry, report, storage, uds
from .connection import ConnectionManager
from .version import __version__

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
    # Inject the app version into the page so About/the footer show it immediately,
    # without depending on the websocket "app" message arriving.
    try:
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        return web.Response(text=html.replace("__FK_VERSION__", __version__),
                            content_type="text/html")
    except OSError:
        return web.FileResponse(STATIC_DIR / "index.html")


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    op = {"task": None}  # the one in-flight cancellable operation

    def log(message: str) -> None:
        if not ws.closed:
            asyncio.create_task(ws.send_json({"type": "log", "message": message}))

    async def send_status() -> None:
        if not ws.closed:
            await ws.send_json({"type": "connection", **_manager.status()})

    def spawn(coro_factory, status_value: str) -> None:
        """Run a long operation as a background task so the websocket loop stays
        free to receive a 'cancel'. Ignored if one is already running."""
        if op["task"] and not op["task"].done():
            return

        async def runner():
            if not ws.closed:
                await ws.send_json({"type": "status", "value": status_value})
            try:
                await coro_factory()
            except asyncio.CancelledError:
                log("Cancelled.")
            except Exception as exc:  # surface failures instead of dying
                log(f"Error: {exc}")
            finally:
                if not ws.closed:
                    await ws.send_json({"type": "status", "value": "idle"})
                await send_status()

        op["task"] = asyncio.create_task(runner())

    _manager.log = log
    if not ws.closed:
        await ws.send_json({"type": "app", "version": __version__,
                            "catalog": dtc_catalog.is_loaded(), "catalog_count": dtc_catalog.count()})
    await send_status()
    # Connect to the dongle right away (even while the first-run disclaimer is
    # up) so it's ready the moment the user accepts. The *scan* is gated client
    # side — it only fires once the user clicks Continue.
    spawn(lambda: _do_connect(ws), "connecting")

    async for msg in ws:
        if msg.type != WSMsgType.TEXT:
            continue
        try:
            payload = json.loads(msg.data)
        except (json.JSONDecodeError, AttributeError):
            continue
        await _handle(payload, ws, log, send_status, op, spawn)

    if op["task"] and not op["task"].done():
        op["task"].cancel()
    return ws


async def _do_connect(ws) -> None:
    """Auto-connect, and if the dongle can't be identified automatically, send the
    scanned device list so the UI can show a picker."""
    if _manager.connected:
        return
    await _manager.connect()
    if not _manager.connected and _manager.needs_pick and not ws.closed:
        await ws.send_json({"type": "device_picker", "devices": _manager.candidates})


async def _connect_device(ws, payload) -> None:
    """Connect to a user-picked device; on failure, rescan and re-offer the picker."""
    ok = await _manager.connect_to(payload.get("address"), payload.get("name"))
    if not ok:
        await _do_connect(ws)  # rescan: may auto-connect now, else re-show the picker


async def _handle(payload, ws, log, send_status, op, spawn) -> None:
    command = payload.get("command")

    # cancel the in-flight connect / scan / clear / discovery
    if command == "cancel":
        if op["task"] and not op["task"].done():
            op["task"].cancel()
        return

    # quick, non-cancellable commands — handled inline
    if command == "code_detail":
        await run_code_detail(ws, payload.get("module"), payload.get("code"))
        return
    if command == "disconnect":
        await _manager.disconnect()
        await send_status()
        return
    if command == "delete_scan":
        if payload.get("scan_id"):
            storage.delete_scan(payload["scan_id"])
        await send_saved_scans(ws)
        return
    if command == "clear_scans":
        storage.clear_scans()
        await send_saved_scans(ws)
        return
    if command == "saved_scans":
        await send_saved_scans(ws)
        return
    if command == "load_scan":
        await send_loaded_scan(ws, payload.get("scan_id"))
        return

    # long, cancellable operations — run as a tracked background task
    long_ops = {
        "connect": (lambda: _do_connect(ws), "connecting"),
        "connect_device": (lambda: _connect_device(ws, payload), "connecting"),
        "read_vin": (lambda: run_read_vin(log), "running"),
        "read_dtcs": (lambda: run_read_dtcs(log), "running"),
        "drill_failing": (lambda: _manager.run(lambda s: uds.drill_failing_dtcs(s, log)), "running"),
        "scan": (lambda: run_scan(ws, log), "running"),
        "discover": (lambda: run_discover(ws, log, full_sweep=bool(payload.get("full_sweep"))), "running"),
        "monitor": (lambda: run_monitor(ws, log, seconds=int(payload.get("seconds", 10))), "running"),
        "functional": (lambda: run_functional(log), "running"),
        "deep_identify": (lambda: run_deep_identify(log), "running"),
        "no_filter": (lambda: run_no_filter(log), "running"),
        "clear_dtcs": (lambda: run_clear_dtcs(ws, log, payload.get("modules", [])), "running"),
        "check_modules": (lambda: run_check_modules(ws, log), "running"),
    }
    entry = long_ops.get(command)
    if entry:
        spawn(entry[0], entry[1])


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


async def run_clear_dtcs(ws, log, module_keys) -> None:
    """Clear DTCs on the chosen modules (UDS 0x14), report per-module results,
    then re-scan so the user sees what actually cleared vs. what came right back."""
    if not module_keys:
        return
    targets = registry.scan_targets()
    attempted = []  # (key, label, known) in request order

    async def _do(s):
        for key in module_keys:
            t = targets.get(key)
            if not t or not t.get("response"):
                attempted.append((key, key.upper(), False))
                continue
            attempted.append((key, t["label"], True))
            log(f"Clearing DTCs on {key.upper()} (0x{t['request']:03X})…")
            try:
                ok, msg = await uds.clear_dtcs(s, t["request"], t["response"], log)
            except Exception as exc:  # never let one module abort the batch
                ok, msg = False, str(exc)
            log(f"  {key.upper()}: {'accepted' if ok else msg}")

    await _manager.run(_do)

    # Source of truth: the clear's own ack lags (radars/cameras reply "response
    # pending" then finish a moment later), so judge each module by what the car
    # ACTUALLY reports after a fresh re-scan, not by the 0x14 reply.
    result = await _run_full_scan(ws, log)
    by_name = {m["name"]: m for m in result.get("modules", [])}
    results = []
    for key, label, known in attempted:
        if not known:
            results.append({"module": key, "label": label, "ok": False, "msg": "unknown module"})
            continue
        m = by_name.get(key)
        status = (m or {}).get("status")
        c = (m or {}).get("counts") or {}
        active = c.get("active", 0) or 0
        hist = c.get("historical", 0) or 0
        if m is None or status in ("unreachable", "not_reached"):
            results.append({"module": key, "label": label, "ok": False,
                            "msg": "no response on re-scan — couldn't confirm"})
        elif active + hist == 0:
            results.append({"module": key, "label": label, "ok": True, "msg": "cleared"})
        elif active:
            results.append({"module": key, "label": label, "ok": False,
                            "msg": f"{active} still active — live fault, fix the cause first"})
        else:
            results.append({"module": key, "label": label, "ok": False, "msg": f"{hist} still present"})
    if not ws.closed:
        await ws.send_json({"type": "clear_result", "results": results})


async def _run_full_scan(ws, log) -> dict:
    """Build a full scan, persist it, push it to the UI, and return the result."""
    def progress(done, total, name):
        if not ws.closed:
            asyncio.create_task(ws.send_json({"type": "scan_progress", "done": done, "total": total, "name": name}))
    result = await _manager.run(lambda s: report.build_scan_result(s, log, progress=progress))
    path = storage.save_scan(result)
    if not ws.closed:
        await ws.send_json({"type": "scan_result", "result": result})
    if path:
        log(f"Saved scan to {path}")
        await send_saved_scans(ws)  # refresh the dropdown with the just-saved scan
    return result


async def run_scan(ws, log) -> None:
    """Full structured scan: VIN + decode + all-module DTCs -> scan_result, saved to disk."""
    await _run_full_scan(ws, log)


async def send_saved_scans(ws) -> None:
    if not ws.closed:
        await ws.send_json({"type": "saved_scans", "scans": storage.list_scans()})


async def send_loaded_scan(ws, scan_id) -> None:
    """Load a saved scan from disk and emit it tagged saved=True so the UI shows
    it as a frozen snapshot (no live actions)."""
    result = storage.load_scan(scan_id) if scan_id else None
    if result and not ws.closed:
        await ws.send_json({"type": "scan_result", "result": result, "saved": True})


async def run_discover(ws, log, full_sweep: bool = False) -> None:
    """Hunt for ECUs and merge into the permanent discovery set.

    The permanent 11-bit modules already found are preserved untouched (no
    re-probe). Every run refreshes the 29-bit extended-addressing pass — the
    only place modules are still missing (rear inverter, VCU, OBC). A full
    11-bit re-sweep (slow) only runs when explicitly requested via full_sweep,
    and even then skips the IDs already known to respond."""
    prior = (storage.load_discovery() or {}).get("ecus", [])
    prior_11 = [e for e in prior if e.get("addressing") != "29bit"]

    new_11: list = []
    if full_sweep:
        skip = {req for _name, (req, _resp) in uds.MODULES.items()}
        skip |= {e["request_id"] for e in prior_11 if e.get("request_id") is not None}
        new_11 = await _manager.run(lambda s: uds.discover_ecus(s, log, skip_requests=skip))
    else:
        log(f"Preserving {len(prior_11)} known 11-bit module(s); refreshing 29-bit pass only "
            f"(use a full re-sweep to re-probe 11-bit).")

    found_29 = await _manager.run(lambda s: uds.discover_ecus_29bit(s, log))

    # Merge: keep prior 11-bit, add any new 11-bit, replace the 29-bit set.
    by_req = {e["request_id"]: e for e in prior_11 if e.get("request_id") is not None}
    for e in new_11:
        by_req.setdefault(e["request_id"], e)
    merged = list(by_req.values()) + found_29

    path = storage.save_discovery(merged)
    if path:
        log(f"Saved discovery to {path}")
    if not ws.closed:
        await ws.send_json({"type": "discovery", "ecus": [_discovery_view(e) for e in merged]})


async def run_no_filter(log) -> None:
    """Wide-open-filter re-sweep — the last blind spot, catching a module that
    answers on a non-(request+8) response ID. Skips IDs already known to respond."""
    skip = {req for _name, (req, _resp) in uds.MODULES.items()}
    for ecu in (storage.load_discovery() or {}).get("ecus", []):
        if ecu.get("addressing") != "29bit" and ecu.get("request_id"):
            skip.add(ecu["request_id"])
    await _manager.run(lambda s: uds.no_filter_sweep(s, log, skip_requests=skip))


async def run_deep_identify(log) -> None:
    """Fingerprint MCU_F and every still-unidentified responder by dumping their
    identification DIDs — to spot the rear inverter / VCU hiding among the
    unknowns (same part-number family as MCU_F)."""
    targets = [("MCU_F (reference)", *uds.MODULES["mcu_f"])]
    for ecu in (storage.load_discovery() or {}).get("ecus", []):
        if ecu.get("addressing") == "29bit" or ecu.get("module") or ecu.get("module_name"):
            continue  # skip 29-bit and anything already named
        req, resp = ecu.get("request_id"), ecu.get("response_id")
        if req and resp:
            pn = ecu.get("part_number")
            label = f"unknown 0x{req:03X}" + (f" [{pn}]" if pn else "")
            targets.append((label, req, resp))
    log(f"Deep identify — fingerprinting {len(targets)} ECU(s) (MCU_F + {len(targets)-1} unknown)…")
    await _manager.run(lambda s: uds.deep_identify(s, log, targets))
    log("Deep identify done — compare the part-number (F187) families above.")


async def run_check_modules(ws, log) -> None:
    """User-facing 'check for extra modules': one fast functional broadcast,
    deduped against everything Fiskeretta knows (built-ins + baked map + this
    car's prior finds). Flags genuinely-unknown responders, persists them so
    future scans include them, and reports a plain summary for the UI."""
    targets = registry.scan_targets()
    known_resp = {t["response"] for t in targets.values() if t.get("response")}
    known_req = {t["request"] for t in targets.values() if t.get("request")}
    responders = await _manager.run(lambda s: uds.functional_probe(s, log, known_responses=known_resp))
    new = [r for r in responders if not r["known"] and r["request_id"] not in known_req]
    if new:
        prior = (storage.load_discovery() or {}).get("ecus", [])
        seen = {e.get("request_id") for e in prior}
        for r in new:
            if r["request_id"] not in seen:
                prior.append({"request_id": r["request_id"], "response_id": r["response_id"],
                              "addressing": "11bit", "module": None, "module_name": None,
                              "part_number": None, "name_hint": None})
        storage.save_discovery(prior)
        log(f"{len(new)} module(s) not in the catalogue — added to this car's scan set.")
    if not ws.closed:
        await ws.send_json({"type": "module_check", "found": len(responders),
                            "new": [{"request_id": r["request_id"], "response_id": r["response_id"]} for r in new]})
    if new:
        await run_scan(ws, log)  # re-scan so the newly-found modules appear


async def run_functional(log) -> None:
    """11-bit functional broadcast (0x7DF) — last-resort probe for ECUs the
    physical UDS sweep missed. Results stream to the log."""
    known = {t["response"] for t in registry.scan_targets().values() if t.get("response")}
    await _manager.run(lambda s: uds.functional_probe(s, log, known_responses=known))


async def run_monitor(ws, log, seconds: int = 10) -> None:
    """Passively sniff the bus and report the arbitration IDs heard."""
    seconds = max(2, min(seconds, 30))
    ids = await _manager.run(lambda s: uds.monitor_bus(s, log, seconds))
    if not ws.closed:
        await ws.send_json({"type": "monitor", "ids": ids, "seconds": seconds})


def _discovery_view(entry: dict) -> dict:
    """Attach a full-name display label (acronyms expanded) for the UI."""
    key = entry.get("module") or entry.get("module_name")
    if key:
        label = modules.friendly(key)
    else:
        label = entry.get("part_number") or f"ECU 0x{entry.get('request_id', 0):X}"
    return {**entry, "label": label}


async def run_code_detail(ws, module, code_str) -> None:
    """On-demand freeze-frame read for one code — when/where it occurred."""
    detail = {"available": False}
    try:
        target = registry.scan_targets().get(module)
        if target:
            detail = await _manager.run(
                lambda s: uds.read_code_detail(s, target["request"], target["response"], int(code_str, 16)))
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
