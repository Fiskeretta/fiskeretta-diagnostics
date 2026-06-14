"""
Scan persistence.

Every scan is written to ~/.config/fiskeretta/scans/ as JSON, so the diagnostic
record survives even after the codes are cleared from the car. The UI's "Saved
scans" section lists them.
"""

import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .paths import CONFIG_DIR, LEGACY_CONFIG_DIR, SCANS_DIR

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
        path.write_text(json.dumps(result, indent=2), encoding="utf-8")
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
            result = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
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
        return json.loads((SCANS_DIR / f"{scan_id}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
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


def save_bms_dids(data: dict) -> Optional[Path]:
    """Persist the latest BMS/live-data DID sweep result (bms_dids.json)."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        path = CONFIG_DIR / "bms_dids.json"
        payload = {"swept_at": datetime.now(timezone.utc).isoformat(), **data}
        path.write_text(json.dumps(payload, indent=2))
        return path
    except OSError:
        return None


def load_bms_dids() -> Optional[dict]:
    try:
        return json.loads((CONFIG_DIR / "bms_dids.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None


def save_discovery(ecus: list) -> Optional[Path]:
    """Persist the latest ECU-discovery result."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        path = CONFIG_DIR / "discovery.json"
        path.write_text(json.dumps(
            {"discovered_at": datetime.now(timezone.utc).isoformat(), "ecus": ecus}, indent=2), encoding="utf-8")
        return path
    except OSError:
        return None


def load_discovery() -> Optional[dict]:
    try:
        return json.loads((CONFIG_DIR / "discovery.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


# --- portability: export / import / native-dir migration -------------------

def _is_safe_member(name: str) -> bool:
    """Only the data files we write, and nothing that escapes CONFIG_DIR
    (guards against zip-slip / arbitrary-file extraction)."""
    if name.endswith("/") or Path(name).is_absolute() or ".." in Path(name).parts:
        return False
    return (name == "events.jsonl"
            or name in ("discovery.json", "bms_dids.json")
            or (name.startswith("scans/") and name.endswith(".json")))


def export_history(dest_path) -> Path:
    """Zip the whole data dir (scans + events + discovery + sweeps) to dest_path."""
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        if CONFIG_DIR.is_dir():
            for p in sorted(CONFIG_DIR.rglob("*")):
                if p.is_file():
                    z.write(p, p.relative_to(CONFIG_DIR).as_posix())
    return dest


def _merge_events(text: str) -> int:
    """Append events from `text` not already present (deduped by when+type)."""
    seen = {(e.get("when"), e.get("type")) for e in list_events()}
    new_lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = (ev.get("when"), ev.get("type"))
        if key in seen:
            continue
        seen.add(key)
        new_lines.append(json.dumps(ev))
    if new_lines:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with EVENTS_FILE.open("a", encoding="utf-8") as f:
            for ln in new_lines:
                f.write(ln + "\n")
    return len(new_lines)


def import_history(zip_path) -> dict:
    """Merge a Fiskeretta history export into CONFIG_DIR, non-destructively: scan
    files are added if missing (timestamped names are unique), events deduped by
    (when, type), discovery/sweep filled only if absent. Raises ValueError if the
    zip isn't a Fiskeretta export."""
    with zipfile.ZipFile(zip_path) as z:
        members = [n for n in z.namelist() if _is_safe_member(n)]
        if not members:
            raise ValueError("not a Fiskeretta history export")
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        scans_added = events_added = 0
        for n in members:
            if n == "events.jsonl":
                events_added += _merge_events(z.read(n).decode("utf-8", "replace"))
                continue
            target = CONFIG_DIR / n
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(n))
            if n.startswith("scans/"):
                scans_added += 1
        return {"scans_added": scans_added, "events_added": events_added}


def migrate_legacy_dir() -> bool:
    """One-time move of ~/.config/fiskeretta to the native CONFIG_DIR: copy to a
    temp dir, verify every file arrived, rename into place, then remove the legacy
    dir. Leaves legacy untouched on any failure. No-op on Linux or once migrated."""
    if CONFIG_DIR == LEGACY_CONFIG_DIR:
        return False
    if CONFIG_DIR.exists() or not LEGACY_CONFIG_DIR.is_dir():
        return False
    tmp = CONFIG_DIR.with_name(CONFIG_DIR.name + ".migrating")
    try:
        CONFIG_DIR.parent.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(LEGACY_CONFIG_DIR, tmp)
        for p in LEGACY_CONFIG_DIR.rglob("*"):  # verify the copy is complete
            if p.is_file() and not (tmp / p.relative_to(LEGACY_CONFIG_DIR)).is_file():
                shutil.rmtree(tmp, ignore_errors=True)
                return False
        tmp.rename(CONFIG_DIR)  # atomic on the same filesystem
        shutil.rmtree(LEGACY_CONFIG_DIR, ignore_errors=True)
        return True
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)
        return False
