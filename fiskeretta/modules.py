"""
Module registry: friendly labels for the ECUs we query, plus the list of
modules the DTC manual documents that we can't reach yet (no CAN address).

The queryable set lives in uds.MODULES (with CAN IDs). NOT_YET_REACHED is shown
in the UI so the picture is complete; the discovery probe finds their addresses
and promotes them. The DTC manual labels its sections with acronyms ("ESP",
"MRR", …); FRIENDLY expands those to full names everywhere they're displayed.
"""

# Acronym / module-key -> full display name. Single source of truth for both the
# hardcoded modules and anything discovery surfaces.
FRIENDLY = {
    "gateway": "Central Gateway",
    "gw": "Central Gateway",
    "bcm": "Body Control Module",
    "icc": "Infotainment Controller",
    "acu": "Airbag Control Unit",
    "esp": "Electronic Stability Program",
    "pkc": "Passive Keyless Entry",
    "vsp": "Vehicle Sound for Pedestrians",
    "mcu_f": "Motor Control Unit, Front",
    "mcu_r": "Motor Control Unit, Rear",
    "piu": "Power Inverter Unit",
    "bms": "Battery Management System",
    "vcu": "Vehicle Control Unit",
    "ibooster": "Brake Booster (iBooster)",
    "eps": "Electric Power Steering",
    "eps2": "Electric Power Steering 2",
    "adas": "Advanced Driver Assistance",
    "tbox": "Telematics Control Unit",
    "fcm": "Front Camera Module",
    "ecc": "Electronic Climate Control",
    "pdu": "Power Distribution Unit",
    "scm": "Seat Control Module",
    "plgm": "Power Liftgate Module",
    "mrr": "Mid-Range Radar",
    "amp_h": "Audio Amplifier",
    "amp_so": "Audio Amplifier (Surround)",
    "pwc_l": "Power Window Controller, Left",
    "pwc_r": "Power Window Controller, Right",
    "ohc": "Overhead Console",
    "dscm": "Driver Seat Control Module",
    "psm": "Passenger Seat Module",
    "rac": "Rear Air Conditioning",
    "trm": "Thermal Regulation Module",
    "mfss": "Multi-Function Steering Switch",
    "cim": "Column Integration Module",
}

# Documented in the DTC manual but not yet reached by discovery (just keys —
# labels come from FRIENDLY so names live in exactly one place).
NOT_YET_REACHED_KEYS = [
    "mcu_r", "bms", "vcu", "ibooster", "eps", "eps2", "adas", "tbox", "fcm",
    "ecc", "pdu", "scm", "plgm", "mrr", "amp_h", "amp_so", "pwc_l", "pwc_r",
    "ohc", "dscm", "psm", "rac", "trm", "mfss", "cim",
]

NOT_YET_REACHED = [(key, FRIENDLY.get(key, key.upper())) for key in NOT_YET_REACHED_KEYS]


def _norm(name: str) -> str:
    return name.lower().replace("-", "_").replace(" ", "_")


def friendly(name: str) -> str:
    """Full display name for a module key/acronym; falls back to the acronym."""
    if not name:
        return name
    return FRIENDLY.get(_norm(name), name.upper())


def label(name: str) -> str:
    return friendly(name)
