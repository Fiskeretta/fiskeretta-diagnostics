"""
Build a single combined DTC catalog from the two source datasets.

Sources (the user's local copies under docs/DTC/, kept out of the repo):
  - dtc_index.json + fisker_dtc.jsonl  — the Fisker troubleshooting manual
    (description, subsystem principle, failure event, possible causes, diagnostic
    steps, note), keyed by the raw 24-bit code.
  - dtc_extended.json — a second community export ("Fisker Ocean Dtc error list")
    with Impact, Limp behaviour, an ordered Repair list, and monitor/reset Trigger
    conditions.

For each code (the union of both), produce ONE merged record. Overlapping
information is reconciled rather than duplicated:
  - description: keep the more complete of the two.
  - cause (export) is dropped when the manual's subsystem/causes already say it.
  - repair (export) is dropped when it just restates the manual's diagnostic steps.
  - everything else is single-source, so it simply carries through.

Output: docs/DTC/dtc_combined.json, keyed by raw 24-bit code (string), only
non-empty fields kept. Re-run after refreshing either source.
"""

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from fiskeretta import dtc_catalog  # noqa: E402

OUT = REPO.parent / "docs" / "DTC" / "dtc_combined.json"


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _similar(a, b):
    """True if a and b say essentially the same thing (equal, or the shorter is
    contained in the longer once normalised) — used to avoid duplicating text."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    short, long = (na, nb) if len(na) <= len(nb) else (nb, na)
    return len(short) >= 8 and short in long


def _clean(s):
    if not s:
        return None
    s = re.sub(r"[\x00-\x1f]+", " ", s)
    return re.sub(r"\s+", " ", s).strip() or None


def merge(code, man, ext):
    man, ext = man or {}, ext or {}

    md, ed = _clean(man.get("description")), _clean(ext.get("description"))
    description = max((md, ed), key=lambda x: len(x or "")) if (md and ed) else (md or ed)

    subsystem = _clean(man.get("subsystem"))
    possible_causes = _clean(man.get("possible_causes"))
    failure_event = _clean(man.get("failure_event"))

    # export 'cause' only if it adds something the manual doesn't already cover
    cause = _clean(ext.get("cause"))
    if cause and any(_similar(cause, x) for x in (subsystem, possible_causes, failure_event, description)):
        cause = None

    steps = _clean(man.get("steps"))
    repair = _clean(ext.get("repair"))
    if repair and steps and _similar(repair, steps):
        repair = None  # manual's diagnostic steps already cover the fix

    rec = {
        "j2012": dtc_catalog.j2012(code),
        "module": _clean(ext.get("module")) or dtc_catalog.module_for_code(code),
        "description": description,
        "subsystem": subsystem,
        "failure_event": failure_event,
        "possible_causes": possible_causes,
        "cause": cause,
        "impact": _clean(ext.get("impact")),
        "limp": _clean(ext.get("limp")),
        "steps": steps,
        "repair": repair,
        "reference": _clean(man.get("reference")),
        "note": _clean(man.get("note")),
        "trigger": _clean(ext.get("trigger")),
        "page": man.get("page"),
        "sources": "+".join([s for s, present in (("manual", man), ("list", ext)) if present]),
    }
    return {k: v for k, v in rec.items() if v not in (None, "", [])}


def main():
    manual = dtc_catalog._load()
    ext = dtc_catalog._load_extended()
    codes = set(manual) | set(ext)

    combined = {}
    for code in codes:
        combined[str(code)] = merge(code, dtc_catalog.troubleshooting(code), ext.get(code))

    OUT.write_text(json.dumps(combined, ensure_ascii=False, indent=0))

    both = len(set(manual) & set(ext))
    deduped_repair = sum(1 for c in codes if (ext.get(c) or {}).get("repair") and not combined[str(c)].get("repair"))
    print(f"manual={len(manual)} list={len(ext)} both={both} combined={len(combined)}")
    print(f"repair lines deduped against manual steps: {deduped_repair}")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
