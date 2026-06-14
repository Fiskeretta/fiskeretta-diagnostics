"""
Battery health verdict — turn decoded live-data signals into an OK / warn / alert
assessment with human reasons, rather than dumping raw numbers.

Thresholds are PROVISIONAL: designed from cross-EV norms and the wavestripe
reference (cell imbalance >= 80 mV early warning; isolation ~500 Ohm/V floor).
They run against whatever signals are present; once the BMS DID sweep is
calibrated (see bms_signals.BATTERY) the decoded values feed straight in. The
signal keys this expects:

    cell_imbalance_mv   cell voltage spread (max - min), millivolts
    isolation_ohm_per_v HV isolation resistance, ohms per volt
    pack_temp_c         HV pack temperature, deg C
    aux_12v             12 V auxiliary battery voltage
    dcdc_v              DC-DC converter output voltage
"""

from typing import Optional

_RANK = {"ok": 0, "warn": 1, "alert": 2}


def _grade(value, bands):
    """bands: ordered list of (level, predicate, message_fmt). First match wins."""
    for level, pred, msg in bands:
        if pred(value):
            return level, msg.format(v=value)
    return "ok", ""


def verdict(signals: Optional[dict]) -> dict:
    """Return {"level": ok|warn|alert|unknown, "reasons": [{signal, value, level,
    message}]}. `level` is the worst of the per-signal grades; "unknown" when no
    known signal is present."""
    signals = signals or {}
    reasons = []

    def grade(key, bands):
        v = signals.get(key)
        if v is None:
            return
        level, msg = _grade(v, bands)
        reasons.append({"signal": key, "value": v, "level": level, "message": msg})

    grade("cell_imbalance_mv", [
        ("alert", lambda v: v >= 150, "Cell imbalance {v:.0f} mV — significant; pack degradation likely"),
        ("warn", lambda v: v >= 80, "Cell imbalance {v:.0f} mV — early-warning threshold (>=80 mV)"),
        ("ok", lambda v: True, "Cell imbalance {v:.0f} mV — healthy"),
    ])
    grade("isolation_ohm_per_v", [
        ("alert", lambda v: v < 500, "Isolation {v:.0f} Ohm/V — below the 500 floor; possible HV isolation fault"),
        ("warn", lambda v: v < 1000, "Isolation {v:.0f} Ohm/V — marginal"),
        ("ok", lambda v: True, "Isolation {v:.0f} Ohm/V — healthy"),
    ])
    grade("pack_temp_c", [
        ("alert", lambda v: v >= 55, "Pack temp {v:.0f} C — very hot"),
        ("warn", lambda v: v >= 45, "Pack temp {v:.0f} C — warm"),
        ("warn", lambda v: v <= -20, "Pack temp {v:.0f} C — very cold"),
        ("ok", lambda v: True, "Pack temp {v:.0f} C — normal"),
    ])
    grade("aux_12v", [
        ("alert", lambda v: v < 11.0, "12 V battery {v:.1f} V — critically low"),
        ("warn", lambda v: v < 11.8, "12 V battery {v:.1f} V — low"),
        ("ok", lambda v: True, "12 V battery {v:.1f} V — healthy"),
    ])
    grade("dcdc_v", [
        ("warn", lambda v: v < 12.0 or v > 15.5, "DC-DC output {v:.1f} V — out of normal range"),
        ("ok", lambda v: True, "DC-DC output {v:.1f} V — normal"),
    ])

    if not reasons:
        return {"level": "unknown", "reasons": []}
    level = max((r["level"] for r in reasons), key=lambda lv: _RANK[lv])
    return {"level": level, "reasons": reasons}
