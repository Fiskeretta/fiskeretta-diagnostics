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

from . import dtc_catalog, modules, uds
from . import vin as vinmod

ACTIVE = "active"
HISTORICAL = "historical"
CLEAR = "clear"
UNREACHABLE = "unreachable"
NOT_REACHED = "not_reached"

_TIER_ORDER = {ACTIVE: 0, HISTORICAL: 1, CLEAR: 2, UNREACHABLE: 3, NOT_REACHED: 4}


def _dtc_dict(d: uds.Dtc) -> dict:
    return {
        "code": f"0x{d.code_hex}",
        "j2012": dtc_catalog.j2012(d.code),
        "status_byte": d.status,
        "failing_now": d.is_failing_now,
        "confirmed": d.is_confirmed,
        "description": d.description,
    }


def build_from_report(report: dict, vehicle: Optional[dict] = None) -> dict:
    """Pure transform: raw {module_name: [Dtc] | None} -> structured result."""
    mods = []
    active_total = historical_total = reachable = 0

    for name, dtcs in report.items():
        lbl = modules.label(name)
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

    for name, lbl in modules.NOT_YET_REACHED:
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
            "modules_total": len(report) + len(modules.NOT_YET_REACHED),
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

    if log:
        log("Scanning all modules for DTCs…")
    report = await uds.read_all_dtcs(session, log=None)
    result = build_from_report(report, vehicle)

    s = result["summary"]
    if log:
        log(f"Scan complete — {s['active']} active, {s['historical']} historical "
            f"across {s['modules_reachable']} reachable modules.")
    return result
