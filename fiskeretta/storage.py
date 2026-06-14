"""
Scan persistence.

Every scan is written to ~/.config/fiskeretta/scans/ as JSON, so the diagnostic
record survives even after the codes are cleared from the car. The UI's "Saved
scans" section lists them.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .paths import CONFIG_DIR, SCANS_DIR

EVENTS_FILE = CONFIG_DIR / "events.jsonl"


def save_scan(result: dict) -> Optional[Path]:
    """Write a scan result to disk, return its path (or None if it couldn't)."""
    try:
        SCANS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = SCANS_DIR / f"scan-{stamp}.json"
        n = 1
        while path.exists():
            path = SCANS_DIR / f"scan-{stamp}-{n}.json"
            n += 1
        path.write_text(json.dumps(result, indent=2))
        return path
    except OSError:
        return None


def list_scans() -> list[dict]:
    """Summaries of saved scans, newest first."""
    if not SCANS_DIR.is_dir():
        return []
    out = []
    for path in sorted(SCANS_DIR.glob("scan-*.json"), reverse=True):
        try:
            result = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        summary = result.get("summary", {})
        out.append({
            "id": path.stem,
            "scanned_at": result.get("scanned_at"),
            "active": summary.get("active"),
            "historical": summary.get("historical"),
            "comm": summary.get("comm"),
        })
    return out


def load_scan(scan_id: str) -> Optional[dict]:
    try:
        return json.loads((SCANS_DIR / f"{scan_id}.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None


def delete_scan(scan_id: str) -> bool:
    try:
        (SCANS_DIR / f"{scan_id}.json").unlink(missing_ok=True)
        return True
    except OSError:
        return False


def clear_scans() -> int:
    """Delete every saved scan; returns how many were removed."""
    if not SCANS_DIR.is_dir():
        return 0
    n = 0
    for path in SCANS_DIR.glob("scan-*.json"):
        try:
            path.unlink()
            n += 1
        except OSError:
            pass
    return n


def record_event(event: dict) -> None:
    """Append one event to the append-only history log (events.jsonl).
    Used for clear events so the timeline shows the clear -> re-accumulate
    pattern alongside scans. Best-effort; never raises."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with EVENTS_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except OSError:
        pass


def record_clear(summary_before: Optional[dict], modules: Optional[list] = None) -> None:
    """Record a DTC clear in the history log, stamping what was wiped (taken from
    the most recent pre-clear scan summary) and which modules were targeted."""
    sb = summary_before or {}
    record_event({
        "when": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00"),
        "type": "clear",
        "wiped": {
            "active": sb.get("active") or 0,
            "historical": sb.get("historical") or 0,
            "comm": sb.get("comm") or 0,
        },
        "modules": modules or [],
    })


def list_events() -> list[dict]:
    """All recorded events (clears), oldest-first as written. Tolerates a missing
    file and skips any corrupt line."""
    if not EVENTS_FILE.exists():
        return []
    out = []
    try:
        for line in EVENTS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return out


def save_discovery(ecus: list) -> Optional[Path]:
    """Persist the latest ECU-discovery result."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        path = CONFIG_DIR / "discovery.json"
        path.write_text(json.dumps(
            {"discovered_at": datetime.now(timezone.utc).isoformat(), "ecus": ecus}, indent=2))
        return path
    except OSError:
        return None


def load_discovery() -> Optional[dict]:
    try:
        return json.loads((CONFIG_DIR / "discovery.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None
