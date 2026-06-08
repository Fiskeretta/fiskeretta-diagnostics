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


def _load() -> dict:
    """Build {numeric_code: row} from the first catalog file we find. Cached."""
    global _catalog
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
