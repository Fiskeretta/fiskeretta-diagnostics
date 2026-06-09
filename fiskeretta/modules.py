"""
Module registry: friendly labels for the ECUs we query, plus the list of
modules the DTC manual documents that we can't reach yet (no CAN address).

The queryable set lives in uds.MODULES (with CAN IDs). NOT_YET_REACHED is shown
in the UI so the picture is complete; the discovery probe (a later phase) will
find their addresses and promote them.
"""

# Friendly labels for the modules we can currently query (uds.MODULES keys).
LABELS = {
    "gateway": "Central Gateway",
    "bcm": "Body Control Module",
    "icc": "Infotainment Controller",
    "acu": "Airbag Control Unit",
    "esp": "Electronic Stability Program",
    "pkc": "Passive Keyless Entry",
    "vsp": "Vehicle Sound for Pedestrians",
    "mcu_f": "Motor Control Unit, Front",
    "piu": "Power Inverter Unit",
}

# Documented in the DTC manual but not yet reachable (no CAN address known).
NOT_YET_REACHED = [
    ("mcu_r", "Motor Control Unit, Rear"),
    ("bms", "Battery Management System"),
    ("vcu", "Vehicle Control Unit"),
    ("ibooster", "Brake Booster (iBooster)"),
    ("eps", "Electric Power Steering"),
    ("eps2", "Electric Power Steering 2"),
    ("adas", "Advanced Driver Assistance"),
    ("tbox", "Telematics Control Unit"),
    ("fcm", "Front Camera Module"),
    ("ecc", "Electronic Climate Control"),
    ("pdu", "Power Distribution Unit"),
    ("scm", "Seat Control Module"),
    ("plgm", "Power Liftgate Module"),
    ("mrr", "Mid-Range Radar"),
    ("amp_h", "Audio Amplifier"),
    ("amp_so", "Audio Amplifier (Surround)"),
    ("pwc_l", "Power Window Controller, Left"),
    ("pwc_r", "Power Window Controller, Right"),
    ("ohc", "Overhead Console"),
    ("dscm", "Driver Seat Control Module"),
    ("psm", "Passenger Seat Module"),
    ("rac", "Rear Air Conditioning"),
    ("trm", "Thermal Regulation Module"),
    ("mfss", "Multi-Function Steering Switch"),
    ("cim", "Column Integration Module"),
]


def label(name: str) -> str:
    return LABELS.get(name, name.upper())
