"""SecOC-strip pattern classification.

On 'ovloop' community firmware the SecOC message authentication is stripped, so
codes whose trip condition is a CRC / alive-counter / missing-message / checksum
check flood the bus with no drivable symptom. `is_secoc_pattern` flags those by
catalog description; report.py surfaces a per-module + summary `secoc` count.
"""
from fiskeretta import report, uds
from fiskeretta.uds import Dtc


_FAKE_DESC = {
    0x110001: "Brake bus ADAS missing message",
    0x110002: "Alive counter mismatch detected on the powertrain CAN",
    0x110003: "CRC check failed over the SecOC tail",
    0x110004: "EEPROM checksum invalid",
    0x110005: "Left front turn lamp open load",   # real fault, no SecOC keyword
}


def _patch(monkeypatch):
    monkeypatch.setattr(uds.dtc_catalog, "describe", lambda code: _FAKE_DESC.get(code))


def test_is_secoc_pattern(monkeypatch):
    _patch(monkeypatch)
    assert Dtc(0x110001, 0x08).is_secoc_pattern   # missing message
    assert Dtc(0x110002, 0x08).is_secoc_pattern   # alive counter
    assert Dtc(0x110003, 0x2F).is_secoc_pattern   # CRC — even when "active"
    assert Dtc(0x110004, 0x08).is_secoc_pattern   # checksum
    assert not Dtc(0x110005, 0x09).is_secoc_pattern   # real fault
    assert not Dtc(0x999999, 0x08).is_secoc_pattern   # no description -> not flagged


def test_report_secoc_count(monkeypatch):
    _patch(monkeypatch)
    report_in = {
        "vcu": [Dtc(0x110001, 0x08), Dtc(0x110002, 0x08), Dtc(0x110005, 0x09)],
        "bcm": [Dtc(0x110004, 0x08)],
    }
    res = report.build_from_report(report_in, labels={"vcu": "VCU", "bcm": "BCM"},
                                   not_reached_list=[])
    by = {m["name"]: m for m in res["modules"]}
    assert by["vcu"]["counts"]["secoc"] == 2          # the two SecOC codes, not the turn lamp
    assert by["bcm"]["counts"]["secoc"] == 1
    assert res["summary"]["secoc"] == 3
    # the per-code dict carries the flag for UI tagging
    vcu_codes = {c["code"]: c["secoc"] for c in by["vcu"]["codes"]}
    assert vcu_codes["0x110001"] is True
    assert vcu_codes["0x110005"] is False
