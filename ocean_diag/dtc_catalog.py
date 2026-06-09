"""
Optional DTC description catalog.

The ocean-diag tool ships with no DTC descriptions of its own — Fisker never
published a public DTC table. If the user has a local copy of the Fisker DTC
manual index (a JSON list of {dtc, ftb, description, page} rows, where `ftb`
is the raw 24-bit numeric code), we load it at runtime and translate scan
results. No catalog present → scans still work, just without descriptions.

The catalog data is deliberately kept *outside* this repository: the tool
stays clean/open, the proprietary manual stays the user's own local copy.
Point OCEAN_DIAG_DTC_CATALOG at a dtc_index.json, or drop one at one of the
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
    os.environ.get("OCEAN_DIAG_DTC_CATALOG"),
    _REPO_ROOT.parent / "docs" / "DTC" / "dtc_index.json",  # sibling docs/ dir (dev)
    _REPO_ROOT / "docs" / "DTC" / "dtc_index.json",
    Path.home() / ".config" / "ocean-diag" / "dtc_index.json",  # user data (packaged)
    Path(sys.executable).parent / "dtc_index.json",            # next to the app binary
]

_catalog: Optional[dict] = None
_catalog_dir: Optional[Path] = None
_pages: Optional[dict] = None


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
    return bool(_load())


def describe(code: int) -> Optional[str]:
    """Return the human description for a raw 24-bit DTC code, or None."""
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
        Path.home() / ".config" / "ocean-diag" / _PAGE_FILENAME,
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
