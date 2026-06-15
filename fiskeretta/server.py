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
import base64
import binascii
import json
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

from aiohttp import web, WSMsgType

from . import bms_signals, diff, dtc_catalog, modules, registry, report, storage, uds
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
    if command == "history":
        await send_history(ws)
        return
    if command == "scan_diff":
        await run_scan_diff(ws, payload.get("scan_id"))
        return
    if command == "code_history":
        await run_code_history(ws, payload.get("module"), payload.get("code"))
        return
    if command == "reveal_folder":
        _reveal_folder()
        return
    if command == "export_history":
        await run_export_history(ws)
        return
    if command == "import_history":
        await run_import_history(ws, payload.get("data"))
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
        "generic_obd": (lambda: run_generic_obd(ws, log), "running"),
        "bms_sweep": (lambda: run_bms_sweep(ws, log, payload), "running"),
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
    # Snapshot the pre-clear state (newest saved scan) so the history timeline can
    # show what was wiped — captured BEFORE the post-clear re-scan is saved.
    pre = storage.list_scans()
    pre_summary = pre[0] if pre else None
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
    # Stamp the clear event now — at clear time, before the re-scan — so it sorts
    # just before (older than) the post-clear scan it triggers in the timeline.
    storage.record_clear(pre_summary, module_keys)

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
        await send_history(ws)  # the clear is now in the timeline


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


def _reveal_folder() -> None:
    """Open the data directory in the OS file manager (best effort)."""
    storage.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path = str(storage.CONFIG_DIR)
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif sys.platform.startswith("win"):
            subprocess.Popen(["explorer", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except OSError:
        pass


def _export_dir() -> Path:
    """A visible place to drop the export: the Desktop if it exists, else home."""
    desktop = Path.home() / "Desktop"
    return desktop if desktop.is_dir() else Path.home()


async def run_export_history(ws) -> None:
    """Zip the data dir to the Desktop and tell the UI where it landed."""
    name = f"Fiskeretta-history-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    try:
        path = storage.export_history(_export_dir() / name)
    except OSError as exc:
        if not ws.closed:
            await ws.send_json({"type": "export_result", "ok": False, "error": str(exc)})
        return
    if not ws.closed:
        await ws.send_json({"type": "export_result", "ok": True, "path": str(path)})


async def run_import_history(ws, data_url) -> None:
    """Import a history zip the UI read client-side and sent as a data URL."""
    if not data_url:
        return
    b64 = data_url.split(",", 1)[1] if "," in data_url else data_url
    tmp = storage.CONFIG_DIR / "_import.tmp.zip"
    try:
        storage.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(base64.b64decode(b64))
        result = storage.import_history(tmp)
    except (ValueError, OSError, zipfile.BadZipFile, binascii.Error) as exc:
        if not ws.closed:
            await ws.send_json({"type": "import_result", "ok": False, "error": str(exc)})
        return
    finally:
        tmp.unlink(missing_ok=True)
    if not ws.closed:
        await ws.send_json({"type": "import_result", "ok": True, **result})
        await send_saved_scans(ws)
        await send_history(ws)


async def send_history(ws) -> None:
    """Merged timeline of saved scans + recorded clear events, newest-first."""
    if not ws.closed:
        items = diff.merge_history(storage.list_scans(), storage.list_events())
        await ws.send_json({"type": "history", "items": items})


async def run_scan_diff(ws, scan_id) -> None:
    """Diff a saved scan against the chronologically previous one."""
    if not scan_id:
        return
    ids = [s["id"] for s in storage.list_scans()]  # newest-first
    if scan_id not in ids:
        return
    curr = storage.load_scan(scan_id)
    if curr is None:
        return
    i = ids.index(scan_id)
    prev_id = ids[i + 1] if i + 1 < len(ids) else None
    prev = storage.load_scan(prev_id) if prev_id else None
    if not ws.closed:
        await ws.send_json({"type": "scan_diff", "scan_id": scan_id, "prev_id": prev_id,
                            "diff": diff.diff_scans(prev, curr)})


async def run_code_history(ws, module, code) -> None:
    """Cross-scan history for one (module, code) across every saved scan."""
    if not module or not code:
        return
    scans = [s for s in (storage.load_scan(x["id"]) for x in storage.list_scans()) if s]
    if not ws.closed:
        await ws.send_json({"type": "code_history", "module": module, "code": code,
                            "history": diff.code_history(scans, module, code)})


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


async def run_generic_obd(ws, log) -> None:
    """Probe for legislated generic OBD (SAE J1979) and record what answers.

    The Ocean does expose a minimal J1979 surface (ECU 0x7CA answers modes
    01/03/09 with a tiny PID set and a masked VIN) — it is NOT UDS-only. Persist
    the result so the finding survives a crash; it's otherwise UI-only."""
    result = await _manager.run(lambda s: uds.probe_generic_obd(s, log))
    path = storage.save_generic_obd(result)
    if path:
        log(f"Saved generic-OBD probe to {path}")
    if not ws.closed:
        await ws.send_json({"type": "generic_obd", **result})


async def run_bms_sweep(ws, log, payload) -> None:
    """Sweep UDS 0x22 DIDs on the BMS (default 0x7E1) to discover live-data
    signals. Range is configurable; one sweep is capped at 4096 DIDs."""
    try:
        start = int(str(payload.get("start", "F180")), 16)
        end = int(str(payload.get("end", "F1FF")), 16)
    except (TypeError, ValueError):
        log("Bad DID range — use hex like F180.")
        return
    start = max(0, min(start, 0xFFFF))   # DIDs are 2-byte identifiers
    end = max(0, min(end, 0xFFFF))
    if end < start:
        start, end = end, start
    end = min(end, start + 4095)  # cap one sweep at 4096 DIDs
    req, resp = 0x7E1, 0x7E9      # BMS; the UI exposes no other target
    dids = range(start, end + 1)
    log(f"Sweeping DIDs 0x{start:04X}–0x{end:04X} on 0x{req:03X} ({len(dids)} DIDs)…")

    def progress(done, total, did):
        if not ws.closed and (done % 16 == 0 or done == total):
            asyncio.create_task(ws.send_json(
                {"type": "scan_progress", "done": done, "total": total, "name": f"DID {did:04X}"}))

    rows = await _manager.run(lambda s: uds.sweep_dids(s, req, resp, dids, log, progress=progress))
    for r in rows:
        dec = bms_signals.decode_hex(r["did"], r["raw"])
        if dec:
            r["decoded"] = dec
    path = storage.save_bms_dids({"request": f"0x{req:03X}", "range": [f"{start:04X}", f"{end:04X}"], "rows": rows})
    if path:
        log(f"Saved DID sweep to {path}")
    if not ws.closed:
        await ws.send_json({"type": "did_sweep", "module": f"0x{req:03X}",
                            "start": f"{start:04X}", "end": f"{end:04X}", "rows": rows})


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
    storage.migrate_legacy_dir()  # one-time move of ~/.config data to the native dir
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_static("/static/", STATIC_DIR)
    return app


def main() -> None:
    web.run_app(build_app(), host="localhost", port=8765)


if __name__ == "__main__":
    main()
