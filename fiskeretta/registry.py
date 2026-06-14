"""
Runtime module registry — the set of modules a scan targets.

Combines the built-in modules (uds.MODULES + friendly labels) with whatever
discovery has found and saved (discovery.json). Once you discover the rest of
the bus, every scan includes those modules automatically — no re-running.
Manual-documented modules not yet discovered are reported as "not yet reached".
"""

from . import modules as _modules
from . import storage
from .knowns import KNOWN_ECUS
from .uds import MODULES


# Addresses positively identified by part number where the DTC-section heuristic
# mislabels them. The rear inverter files its codes under the front MCU's manual
# section, so discovery tags 0x7F2 "MCU_F"; its part number (…0041, vs the front's
# …0040) confirms it's the rear unit.
ADDRESS_OVERRIDES = {
    0x7F2: ("mcu_r", "Motor Control Unit, Rear"),
}


def _norm(name: str) -> str:
    """Normalize a module name to a key (lowercase, hyphens/spaces -> underscore)."""
    return name.lower().replace("-", "_").replace(" ", "_")


def _discovered_ecus() -> list:
    """The catalogued 11-bit ECUs: the baked canonical map (knowns.KNOWN_ECUS)
    as the baseline, plus anything this install has found at runtime
    (discovery.json) layered on top. So a fresh install scans the full known set
    out of the box; runtime finds augment/override by request id. 29-bit finds
    stay out of the scan path (the DTC reader addresses on 11-bit protocol 6)."""
    data = storage.load_discovery()
    runtime = data.get("ecus", []) if data else []
    by_req: dict = {}
    for ecu in list(KNOWN_ECUS) + runtime:  # runtime layered last → wins
        if ecu.get("addressing") == "29bit":
            continue
        req = ecu.get("request_id")
        if req is not None:
            by_req[req] = ecu
    return list(by_req.values())


def scan_targets() -> dict:
    """key -> {label, request, response} for every module a scan should query."""
    targets: dict = {}
    seen_requests = set()

    for name, (req, resp) in MODULES.items():
        targets[name] = {"label": _modules.label(name), "request": req, "response": resp}
        seen_requests.add(req)

    for ecu in _discovered_ecus():
        req = ecu.get("request_id")
        if req is None or req in seen_requests:
            continue
        seen_requests.add(req)
        if req in ADDRESS_OVERRIDES:
            key, lbl = ADDRESS_OVERRIDES[req]
        else:
            mod_name = ecu.get("module_name")
            key = _norm(mod_name) if mod_name else f"ecu_{req:03x}"
            lbl = _modules.friendly(mod_name) if mod_name else f"ECU 0x{req:03X}"
        if key in targets:  # multiple ECUs share a name (e.g. four radars) — keep both
            key = f"{key}_{req:03x}"
        targets[key] = {"label": lbl, "request": req, "response": ecu.get("response_id")}
    return targets


def label(key: str) -> str:
    target = scan_targets().get(key)
    return target["label"] if target else key.upper()


def not_reached() -> list:
    """Manual-documented modules not yet discovered, as (key, label) pairs."""
    ecus = _discovered_ecus()
    discovered = {_norm(e["module_name"]) for e in ecus if e.get("module_name")}
    discovered |= set(MODULES.keys())
    # Apply address overrides so a mislabeled find (e.g. 0x7F2 → mcu_r) clears the
    # correct entry from the not-yet-reached list.
    present = {e.get("request_id") for e in ecus}
    discovered |= {key for req, (key, _lbl) in ADDRESS_OVERRIDES.items() if req in present}
    return [(key, lbl) for key, lbl in _modules.NOT_YET_REACHED if key not in discovered]
