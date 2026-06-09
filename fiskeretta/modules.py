"""
Module registry: friendly labels for the ECUs we query, plus the list of
modules the DTC manual documents that we can't reach yet (no CAN address).

The queryable set lives in uds.MODULES (with CAN IDs). NOT_YET_REACHED is shown
in the UI so the picture is complete; the discovery probe (a later phase) will
find their addresses and promote them.
"""

# Friendly labels for the modules we can currently query (uds.MODULES keys).
LABELS = {
    "gateway": "Gateway",
    "bcm": "Body Control",
    "icc": "Infotainment",
    "acu": "Airbags",
    "esp": "Stability & Brakes",
    "pkc": "Keyless Entry",
    "vsp": "Pedestrian Sound",
    "mcu_f": "Front Motor",
    "piu": "Power Inverter",
}

# Documented in the DTC manual but not yet reachable (no CAN address known).
NOT_YET_REACHED = [
    ("mcu_r", "Rear Motor"),
    ("bms", "Battery Management"),
    ("vcu", "Vehicle Control"),
    ("ibooster", "Brake Booster"),
    ("eps", "Power Steering"),
    ("eps2", "Power Steering 2"),
    ("adas", "Driver Assist"),
    ("tbox", "Telematics"),
    ("fcm", "Front Camera"),
    ("ecc", "Climate"),
    ("pdu", "Power Distribution"),
    ("scm", "Seat Control"),
    ("plgm", "Power Liftgate"),
    ("mrr", "Mid-Range Radar"),
    ("amp_h", "Audio Amp"),
    ("amp_so", "Audio Amp 2"),
    ("pwc_l", "Window Ctrl L"),
    ("pwc_r", "Window Ctrl R"),
    ("ohc", "Overhead Console"),
    ("dscm", "Driver Seat"),
    ("psm", "Passenger Seat"),
    ("rac", "Rear Climate"),
    ("trm", "Thermal"),
    ("mfss", "Steering Switches"),
    ("cim", "Column Integration"),
]


def label(name: str) -> str:
    return LABELS.get(name, name.upper())
