"""
Named decoders for UDS DIDs surfaced by the BMS / live-data sweep.

The high-value EV-owner signals (HV SoC, SoH, cell-voltage spread, pack/motor
temps, isolation resistance, DC-DC, 12 V) live in Fisker-specific DIDs that are
NOT publicly documented — the OBDb signal set is an empty stub. They must be
discovered with the DID sweep and calibrated against the dashboard before a
formula goes here (the same method used to lock the odometer DID).

Until then this only decodes the standard ISO 14229 identification block, so the
sweep table shows friendly values for the DIDs we already understand. Add battery
signals to BATTERY (with a calibrated decoder) as each is confirmed.
"""

from typing import Optional


def _ascii(data: bytes) -> str:
    return "".join(c for c in data.decode("ascii", errors="ignore") if c.isprintable()).strip()


# Confirmed, calibrated signals: did -> (name, decoder, unit).
IDENTIFICATION = {
    0xF190: ("VIN", _ascii, None),
    0xF187: ("Spare part number", _ascii, None),
    0xF197: ("System name", _ascii, None),
    0xF188: ("Software version", _ascii, None),
    0xF191: ("Hardware version", _ascii, None),
    0xF18C: ("Serial number", _ascii, None),
}

# Battery / live-data signals go here once a DID is discovered AND its scaling is
# calibrated against the dashboard. Empty by design — do not guess scalings.
# Example shape once confirmed:
#   0xXXXX: ("HV State of Charge", lambda d: round(d[0] / 2.55, 1), "%"),
BATTERY: dict = {}

KNOWN = {**IDENTIFICATION, **BATTERY}


def decode(did: int, data: bytes) -> Optional[dict]:
    """Friendly decode for a known DID, else None. `data` is the bytes after the
    2-byte DID tag (what sweep_dids returns as `raw`)."""
    entry = KNOWN.get(did)
    if not entry:
        return None
    name, decoder, unit = entry
    try:
        value = decoder(data)
    except Exception:
        return None
    return {"name": name, "value": value, "unit": unit}


def decode_hex(did_hex: str, raw_hex: str) -> Optional[dict]:
    """Convenience wrapper taking the hex strings sweep_dids produces."""
    try:
        return decode(int(did_hex, 16), bytes.fromhex(raw_hex))
    except (ValueError, TypeError):
        return None
