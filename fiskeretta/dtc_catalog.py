"""
Optional DTC description catalog.

The fiskeretta tool ships with no DTC descriptions of its own — Fisker never
published a public DTC table. If the user has a local copy of the Fisker DTC
manual index (a JSON list of {dtc, ftb, description, page} rows, where `ftb`
is the raw 24-bit numeric code), we load it at runtime and translate scan
results. No catalog present → scans still work, just without descriptions.

The catalog data is deliberately kept *outside* this repository: the tool
stays clean/open, the proprietary manual stays the user's own local copy.
Point FISKERETTA_DTC_CATALOG at a dtc_index.json, or drop one at one of the
default search paths below.
"""

import json
import os
import sys
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Search order: explicit env var, then conventional locations (dev tree first,
# then packaged-app locations a user can drop the catalog into).
_CANDIDATES = [
    os.environ.get("FISKERETTA_DTC_CATALOG"),
    _REPO_ROOT.parent / "docs" / "DTC" / "dtc_index.json",  # sibling docs/ dir (dev)
    _REPO_ROOT / "docs" / "DTC" / "dtc_index.json",
    Path.home() / ".config" / "fiskeretta" / "dtc_index.json",  # user data (packaged)
    Path(sys.executable).parent / "dtc_index.json",            # next to the app binary
]

_catalog: Optional[dict] = None
_catalog_dir: Optional[Path] = None
_pages: Optional[dict] = None
_extended: Optional[dict] = None
_combined: Optional[dict] = None


def _load() -> dict:
    """Build {numeric_code: row} from the first catalog file we find. Cached."""
    global _catalog, _catalog_dir
    if _catalog is not None:
        return _catalog

    _catalog = {}
    for candidate in _CANDIDATES:
        if not candidate:
            continue
        path = Path(candidate)
        if not path.is_file():
            continue
        try:
            rows = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        for row in rows:
            try:
                _catalog[int(row["ftb"])] = row
            except (KeyError, ValueError, TypeError):
                continue
        _catalog_dir = path.parent
        break  # first usable catalog wins
    return _catalog


def is_loaded() -> bool:
    return bool(_load_combined() or _load())


def count() -> int:
    """How many codes the active catalog can describe — shown in the About box."""
    return len(_load_combined()) or len(_load())


def describe(code: int) -> Optional[str]:
    """Return the human description for a raw 24-bit DTC code, or None."""
    rec = lookup(code)
    if rec and rec.get("description"):
        return rec["description"]
    row = _load().get(code)
    return row.get("description") if row else None


def page(code: int) -> Optional[int]:
    row = _load().get(code)
    return row.get("page") if row else None


def j2012(code: int) -> str:
    """Render a raw 24-bit DTC as its SAE J2012 string (e.g. 0xA21715 ->
    'B221715'), matching the manual's `dtc` column. Letter from the top two
    bits of the first byte: 00=P, 01=C, 10=B, 11=U."""
    b0, b1, b2 = (code >> 16) & 0xFF, (code >> 8) & 0xFF, code & 0xFF
    letter = "PCBU"[(b0 >> 6) & 0x3]
    return f"{letter}{(b0 >> 4) & 0x3}{b0 & 0xF:X}{b1:02X}{b2:02X}"


# --- full troubleshooting (manual page text) -------------------------------

# The manual's per-page text (fisker_dtc.jsonl: {page, idx, text}) carries the
# full writeup per DTC. We join code -> page (from the index) -> page text, then
# slice the labelled sections out of it.
_PAGE_FILENAME = "fisker_dtc.jsonl"
_SECTION_LABELS = [
    ("subsystem", "Subsystem Principle:"),
    ("failure_event", "Failure Event:"),
    ("possible_causes", "Possible Causes:"),
    ("reference", "Reference Information:"),
    ("steps", "Diagnostic Test Steps:"),
    ("note", "Note:"),
]


def _load_pages() -> dict:
    """Build {page_number: page_text} from the manual jsonl, if present. Cached."""
    global _pages
    if _pages is not None:
        return _pages

    _load()  # ensure _catalog_dir is resolved
    _pages = {}
    candidates = [
        _catalog_dir / _PAGE_FILENAME if _catalog_dir else None,
        _REPO_ROOT.parent / "docs" / "DTC" / _PAGE_FILENAME,
        Path.home() / ".config" / "fiskeretta" / _PAGE_FILENAME,
    ]
    for cand in candidates:
        if not cand or not Path(cand).is_file():
            continue
        try:
            for line in Path(cand).read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                _pages[row.get("page")] = row.get("text", "")
        except (OSError, json.JSONDecodeError):
            _pages = {}
            continue
        break
    return _pages


def troubleshooting(code: int) -> Optional[dict]:
    """Full troubleshooting for a code: description plus the manual's
    subsystem principle, failure event, possible causes, diagnostic steps, and
    note (whichever the page provides). Returns None if the code isn't in the
    catalog; returns just {description, page} if the page text isn't available."""
    row = _load().get(code)
    if not row:
        return None

    pg = row.get("page")
    result = {"page": pg, "description": row.get("description")}
    text = _load_pages().get(pg)
    if not text:
        return result

    # Locate each section label, then slice text between consecutive labels.
    found = []
    for key, label in _SECTION_LABELS:
        idx = text.find(label)
        if idx != -1:
            found.append((idx, key, len(label)))
    found.sort()
    for i, (idx, key, label_len) in enumerate(found):
        start = idx + label_len
        end = found[i + 1][0] if i + 1 < len(found) else len(text)
        value = text[start:end].strip()
        if value:
            result[key] = value
    return result


# --- extended dataset (second DTC export: impact / limp / repair / trigger) ---

# A second community DTC export ("Fisker Ocean Dtc error list") carries fields the
# troubleshooting manual lacks — notably Impact (consequence), Limp (what the car
# does when the fault is active), an ordered Repair list, and the monitor/reset
# Trigger conditions. Parsed into dtc_extended.json keyed by the same raw 24-bit
# code, so it merges with the manual catalog at lookup time.
_EXTENDED_FILENAME = "dtc_extended.json"


def _load_extended() -> dict:
    """Build {numeric_code: {impact, limp, repair, cause, trigger, ...}} from the
    extended export, if present. Cached. Keyed by raw 24-bit int like the manual."""
    global _extended
    if _extended is not None:
        return _extended

    _load()  # resolve _catalog_dir
    _extended = {}
    candidates = [
        _catalog_dir / _EXTENDED_FILENAME if _catalog_dir else None,
        _REPO_ROOT.parent / "docs" / "DTC" / _EXTENDED_FILENAME,
        _REPO_ROOT / "docs" / "DTC" / _EXTENDED_FILENAME,
        Path.home() / ".config" / "fiskeretta" / _EXTENDED_FILENAME,
    ]
    for cand in candidates:
        if not cand or not Path(cand).is_file():
            continue
        try:
            data = json.loads(Path(cand).read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for key, row in data.items():
            try:
                _extended[int(key)] = row
            except (ValueError, TypeError):
                continue
        break
    return _extended


def extended(code: int) -> Optional[dict]:
    """Extended fields for a raw 24-bit DTC from the second export, or None.
    Keys: description, cause, repair, impact, limp, trigger, module, j2012."""
    return _load_extended().get(code)


# --- combined catalog (single baked file, the primary source) --------------

# tools/build_dtc_combined.py merges the manual + extended export into one record
# per code (deduping overlaps) and writes dtc_combined.json. When present it is
# the single source of truth; otherwise we fall back to merging the two legacy
# sources at runtime, so the tool still works with only the manual installed.
_COMBINED_FILENAME = "dtc_combined.json"


def _load_combined() -> dict:
    """Build {numeric_code: merged_record} from the baked combined file. Cached."""
    global _combined
    if _combined is not None:
        return _combined

    _load()  # resolve _catalog_dir
    _combined = {}
    candidates = [
        # Bundled-with-the-app copy: this is what ships in the packaged build
        # (collect_data_files picks up fiskeretta/data/*) and also works from
        # source. __file__ resolves under sys._MEIPASS when frozen.
        Path(__file__).resolve().parent / "data" / _COMBINED_FILENAME,
        _catalog_dir / _COMBINED_FILENAME if _catalog_dir else None,
        _REPO_ROOT.parent / "docs" / "DTC" / _COMBINED_FILENAME,
        _REPO_ROOT / "docs" / "DTC" / _COMBINED_FILENAME,
        Path.home() / ".config" / "fiskeretta" / _COMBINED_FILENAME,
    ]
    for cand in candidates:
        if not cand or not Path(cand).is_file():
            continue
        try:
            data = json.loads(Path(cand).read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for key, row in data.items():
            try:
                _combined[int(key)] = row
            except (ValueError, TypeError):
                continue
        break
    return _combined


def lookup(code: int) -> Optional[dict]:
    """The full merged record for a code: description, module, manual
    troubleshooting (subsystem/failure_event/steps/note), and the export's
    impact/limp/repair/trigger. Reads the baked combined file when present, else
    assembles the same shape from the two legacy sources at runtime."""
    combined = _load_combined()
    if combined:
        return combined.get(code)

    ts = troubleshooting(code)
    ext = _load_extended().get(code)
    if not ts and not ext:
        return None
    rec = dict(ts or {})
    if ext:
        for k in ("impact", "limp", "repair", "cause", "trigger"):
            if ext.get(k):
                rec[k] = ext[k]
        rec.setdefault("description", ext.get("description"))
        if ext.get("module"):
            rec.setdefault("module", ext["module"])
    return rec


def module_for_code(code: int) -> Optional[str]:
    """The manual's module-section name for a code (e.g. 'MCU_R', 'BMS'), taken
    from the page header '<MODULE> DTC Troubleshooting'. Lets us identify a
    discovered ECU by reading one of its codes and looking up which section it
    lives in."""
    rec = _load_combined().get(code)
    if rec and rec.get("module"):
        return rec["module"]
    row = _load().get(code)
    if not row:
        return None
    text = _load_pages().get(row.get("page"))
    if not text:
        return None
    marker = "DTC Troubleshooting"
    for line in text.strip().splitlines():
        line = line.strip()
        if line.endswith(marker):
            return line[: -len(marker)].strip() or None
    return None
