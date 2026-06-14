"""
History helpers — pure transforms over saved scan results and the events log.

These power the History view: a scan-level timeline with diff-on-select, and a
per-code recurrence history. Kept dependency-free and side-effect-free so they
are trivially unit-testable against real saved-scan JSON.
"""

from typing import Optional


def _index(scan: dict) -> dict:
    """(module_name, code) -> code dict, for every noteworthy code in a scan."""
    out = {}
    for mod in scan.get("modules", []):
        for c in mod.get("codes", []):
            out[(mod.get("name"), c.get("code"))] = c
    return out


def _row(module: str, c: dict) -> dict:
    return {
        "module": module,
        "code": c.get("code"),
        "j2012": c.get("j2012"),
        "tier": c.get("tier", "fault"),
        "failing_now": c.get("failing_now"),
    }


def diff_scans(prev: Optional[dict], curr: dict) -> dict:
    """What changed between two scans, by (module, code) presence.

    Returns real (non-comm) faults that appeared/resolved as lists, and the
    communication-tier churn as plain counts so the UI can collapse it into a
    single line. With no previous scan, everything in `curr` counts as appeared.
    """
    p = _index(prev) if prev else {}
    n = _index(curr or {})
    appeared, resolved = [], []
    comm_appeared = comm_resolved = 0

    for key, c in n.items():
        if key in p:
            continue
        if c.get("tier") == "comm":
            comm_appeared += 1
        else:
            appeared.append(_row(key[0], c))

    for key, c in p.items():
        if key in n:
            continue
        if c.get("tier") == "comm":
            comm_resolved += 1
        else:
            resolved.append(_row(key[0], c))

    appeared.sort(key=lambda r: (r["module"] or "", r["code"] or ""))
    resolved.sort(key=lambda r: (r["module"] or "", r["code"] or ""))
    return {
        "appeared": appeared,
        "resolved": resolved,
        "comm_appeared": comm_appeared,
        "comm_resolved": comm_resolved,
    }


def code_history(scans: list, module: str, code: str) -> list:
    """Cross-scan series for one (module, code): every scan where it appears,
    with its status, newest-first. `scans` are full scan-result dicts."""
    out = []
    for scan in scans:
        for mod in scan.get("modules", []):
            if mod.get("name") != module:
                continue
            for c in mod.get("codes", []):
                if c.get("code") == code:
                    out.append({
                        "when": scan.get("scanned_at"),
                        "failing_now": c.get("failing_now"),
                        "tier": c.get("tier", "fault"),
                        "status_byte": c.get("status_byte"),
                    })
    out.sort(key=lambda e: e["when"] or "", reverse=True)
    return out


def recurrence_summary(records: list) -> dict:
    """Summarise a code's on-car freeze-frame occurrence records (from
    uds.read_code_detail): how many times, and the odometer span."""
    records = records or []
    odos = [r["odometer_mi"] for r in records if r.get("odometer_mi") is not None]
    return {
        "count": len(records),
        "first_mi": min(odos) if odos else None,
        "latest_mi": max(odos) if odos else None,
        "recurring": len(records) > 1,
    }


def merge_history(scans: list, events: list) -> list:
    """Merge saved-scan summaries and recorded events into one timeline,
    newest-first. Scans come from storage.list_scans(); events from
    storage.list_events()."""
    items = []
    for s in scans:
        items.append({
            "kind": "scan",
            "when": s.get("scanned_at"),
            "id": s.get("id"),
            "active": s.get("active"),
            "historical": s.get("historical"),
            "comm": s.get("comm"),
        })
    for e in events:
        items.append({
            "kind": e.get("type", "event"),
            "when": e.get("when"),
            "wiped": e.get("wiped"),
            "modules": e.get("modules"),
        })
    items.sort(key=lambda x: x["when"] or "", reverse=True)
    return items
