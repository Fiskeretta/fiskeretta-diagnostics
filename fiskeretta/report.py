"""
Turn a raw DTC scan into the structured result the UI binds to.

Shape:
    {
      "scanned_at": iso8601,
      "vehicle": { vin, year, make, model, series, drive_type, ... },
      "summary": { active, historical, modules_reachable, modules_total },
      "modules": [ { name, label, status, counts:{active,historical}, codes:[...] } ]
    }

Modules are tiered and sorted: active first, then historical, healthy,
unreachable, and finally the not-yet-reached modules. Only "noteworthy" codes
(failing-now or confirmed) are listed per module — the hundreds of routine
"monitor never ran" entries are summarized away.
"""

from datetime import datetime, timezone
from typing import Optional

from . import dtc_catalog, modules, registry, uds
from . import vin as vinmod

ACTIVE = "active"
HISTORICAL = "historical"
CLEAR = "clear"
UNREACHABLE = "unreachable"
NOT_REACHED = "not_reached"

_TIER_ORDER = {ACTIVE: 0, HISTORICAL: 1, CLEAR: 2, UNREACHABLE: 3, NOT_REACHED: 4}


def _dtc_dict(d: uds.Dtc) -> dict:
    info = dtc_catalog.troubleshooting(d.code) or {}
    return {
        "code": f"0x{d.code_hex}",
        "j2012": dtc_catalog.j2012(d.code),
        "status_byte": d.status,
        "failing_now": d.is_failing_now,
        "confirmed": d.is_confirmed,
        "description": d.description,
        # Full troubleshooting (offline, from the manual) for the drill-down.
        "page": info.get("page"),
        "subsystem": info.get("subsystem"),
        "failure_event": info.get("failure_event"),
        "steps": info.get("steps"),
        "note": info.get("note"),
    }


def build_from_report(report: dict, vehicle: Optional[dict] = None,
                      labels: Optional[dict] = None,
                      not_reached_list: Optional[list] = None) -> dict:
    """Pure transform: raw {module_key: [Dtc] | None} -> structured result.
    `labels` maps key -> display label; `not_reached_list` is the (key, label)
    list of manual modules not yet discovered."""
    mods = []
    active_total = historical_total = reachable = 0
    nr = not_reached_list if not_reached_list is not None else modules.NOT_YET_REACHED

    for name, dtcs in report.items():
        lbl = (labels.get(name) if labels else None) or modules.label(name)
        if dtcs is None:
            mods.append({"name": name, "label": lbl, "status": UNREACHABLE,
                         "counts": {"active": 0, "historical": 0}, "codes": []})
            continue
        reachable += 1
        active = sum(1 for d in dtcs if d.is_failing_now)
        historical = sum(1 for d in dtcs if d.is_confirmed and not d.is_failing_now)
        codes = [_dtc_dict(d) for d in dtcs if d.is_noteworthy]
        codes.sort(key=lambda c: (not c["failing_now"], not c["confirmed"], c["code"]))
        status = ACTIVE if active else (HISTORICAL if historical else CLEAR)
        active_total += active
        historical_total += historical
        mods.append({"name": name, "label": lbl, "status": status,
                     "counts": {"active": active, "historical": historical}, "codes": codes})

    for name, lbl in nr:
        mods.append({"name": name, "label": lbl, "status": NOT_REACHED,
                     "counts": {"active": 0, "historical": 0}, "codes": []})

    mods.sort(key=lambda m: (_TIER_ORDER[m["status"]],
                             -m["counts"]["active"], -m["counts"]["historical"], m["label"]))

    return {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "vehicle": vehicle or {},
        "summary": {
            "active": active_total,
            "historical": historical_total,
            "modules_reachable": reachable,
            "modules_total": len(report) + len(nr),
        },
        "modules": mods,
    }


async def build_scan_result(session, log=None) -> dict:
    """Run a full scan against a live session: VIN + decode, then all DTCs."""
    vehicle = {}
    try:
        if log:
            log("Reading VIN…")
        vin_str = await uds.read_vin(session)
        vehicle = vinmod.decode(vin_str)
        if log:
            desc = " ".join(str(vehicle.get(k)) for k in ("year", "make", "model", "series") if vehicle.get(k))
            log(f"VIN {vin_str} — {desc}" if desc else f"VIN {vin_str}")
    except Exception as exc:
        if log:
            log(f"VIN read failed: {exc}")

    targets_full = registry.scan_targets()
    targets = {k: (t["request"], t["response"]) for k, t in targets_full.items()}
    labels = {k: t["label"] for k, t in targets_full.items()}
    if log:
        log(f"Scanning {len(targets)} modules for DTCs…")
    report = await uds.read_all_dtcs(session, log=None, targets=targets)
    result = build_from_report(report, vehicle, labels=labels, not_reached_list=registry.not_reached())

    s = result["summary"]
    if log:
        log(f"Scan complete — {s['active']} active, {s['historical']} historical "
            f"across {s['modules_reachable']} reachable modules.")
    return result
