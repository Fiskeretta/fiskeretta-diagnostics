"""
VIN decoding.

Model year is derivable offline from VIN position 10. Full make/model/trim/
drivetrain come from NHTSA's free vPIC API (no key), cached to disk so we only
hit the network once per VIN — important since the app is usually used in a
garage where connectivity is spotty.
"""

import json
import ssl
import urllib.request
from typing import Optional

from .paths import CONFIG_DIR

# Model-year codes for VIN position 10, 2010-2030 (excludes I, O, Q, U, Z).
_YEAR_CODES = "ABCDEFGHJKLMNPRSTVWXY"
_CACHE = CONFIG_DIR / "vin_cache.json"


def model_year(vin: str) -> Optional[int]:
    """Decode the model year from VIN position 10 (offline). For a Fisker Ocean
    (2022-2024) the 30-year code cycle is unambiguous."""
    if not vin or len(vin) < 10:
        return None
    idx = _YEAR_CODES.find(vin[9].upper())
    return 2010 + idx if idx >= 0 else None


def _ssl_context() -> ssl.SSLContext:
    # The python.org macOS build doesn't trust the system store; use certifi
    # when available, fall back to unverified for a read-only public API.
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl._create_unverified_context()


def _cache_get(vin: str) -> Optional[dict]:
    try:
        return json.loads(_CACHE.read_text()).get(vin)
    except (OSError, json.JSONDecodeError):
        return None


def _cache_put(vin: str, data: dict) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        cache = {}
        if _CACHE.is_file():
            try:
                cache = json.loads(_CACHE.read_text())
            except json.JSONDecodeError:
                cache = {}
        cache[vin] = data
        _CACHE.write_text(json.dumps(cache, indent=2))
    except OSError:
        pass


def decode(vin: str, timeout: float = 15.0) -> dict:
    """Return a vehicle dict for a VIN. Always includes at least {vin, year}
    (offline); adds make/model/series/drivetrain from vPIC when reachable.
    Network result is cached per VIN."""
    base = {"vin": vin, "year": model_year(vin)}
    if not vin:
        return base

    cached = _cache_get(vin)
    if cached is not None:
        return {**base, **cached}

    try:
        url = f"https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}?format=json"
        with urllib.request.urlopen(url, context=_ssl_context(), timeout=timeout) as resp:
            row = json.load(resp)["Results"][0]
    except Exception:
        return base  # offline or API error — the offline year still stands

    decoded = {
        "make": row.get("Make"),
        "model": row.get("Model"),
        "year": int(row["ModelYear"]) if row.get("ModelYear") else base["year"],
        "series": row.get("Series"),
        "drive_type": row.get("DriveType"),
        "ev_drive": row.get("EVDriveUnit"),
        "body": row.get("BodyClass"),
        "plant": ", ".join(p for p in (row.get("PlantCity"), row.get("PlantCountry")) if p) or None,
    }
    decoded = {k: v for k, v in decoded.items() if v}
    _cache_put(vin, decoded)
    return {**base, **decoded}
