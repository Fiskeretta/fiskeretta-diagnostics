"""Phase 1 — comm-tier classification.

A network (U) code that is confirmed but not currently failing is the benign
bus-timeout noise that re-accumulates on a parked car; it should classify as
`comm`. An actively-failing U code stays a real fault.
"""
from fiskeretta import report
from fiskeretta.uds import Dtc


def mk(code, status):
    return Dtc(code=code, status=status)


def test_category_letter():
    assert mk(0xD14281, 0x2E).category == "U"   # U114281
    assert mk(0xA21401, 0x28).category == "B"   # B221401
    assert mk(0x118700, 0x09).category == "P"   # P118700


def test_is_comm_classification():
    # U, confirmed (0x08), not failing (0x01 clear) -> comm
    assert mk(0xD14281, 0x2E).is_comm is True
    # U but failing now -> real fault, not comm
    assert mk(0xD14987, 0x2F).is_comm is False
    # B code, confirmed not failing -> historical fault, not comm (not network)
    assert mk(0xA21401, 0x28).is_comm is False
    # P code, failing -> fault
    assert mk(0x118700, 0x09).is_comm is False


def test_build_from_report_tiers_and_counts():
    report_in = {
        "mcu_f": [mk(0xD14987, 0x2F), mk(0xD14281, 0x2E), mk(0xD14381, 0x2E)],
        "bcm": [mk(0xA21401, 0x28)],
    }
    res = report.build_from_report(
        report_in, labels={"mcu_f": "MCU_F", "bcm": "BCM"}, not_reached_list=[])
    mods = {m["name"]: m for m in res["modules"]}

    mcu = mods["mcu_f"]
    assert mcu["counts"]["active"] == 1
    assert mcu["counts"]["historical"] == 2
    assert mcu["counts"]["comm"] == 2
    tiers = {c["code"]: c["tier"] for c in mcu["codes"]}
    assert tiers["0xD14987"] == "fault"
    assert tiers["0xD14281"] == "comm"
    assert tiers["0xD14381"] == "comm"

    bcm = mods["bcm"]
    assert bcm["counts"]["historical"] == 1
    assert bcm["counts"]["comm"] == 0
    assert {c["code"]: c["tier"] for c in bcm["codes"]}["0xA21401"] == "fault"

    assert res["summary"]["active"] == 1
    assert res["summary"]["historical"] == 3
    assert res["summary"]["comm"] == 2


def test_old_scan_shape_has_comm_added():
    # Sanity: every module's counts now carries a comm key (back-compat surface).
    res = report.build_from_report({"gw": [mk(0x118700, 0x09)]},
                                   labels={"gw": "GW"}, not_reached_list=[])
    assert "comm" in res["modules"][0]["counts"]
    assert "comm" in res["summary"]


def test_comm_counts_with_unreachable_and_not_reached():
    # A None module (unreachable) and a not-reached entry must carry comm:0 and
    # not perturb the summary comm total contributed by reachable modules.
    report_in = {
        "bms": None,                       # unreachable
        "mcu_f": [mk(0xD14281, 0x2E)],     # one comm code
    }
    res = report.build_from_report(
        report_in, labels={"bms": "BMS", "mcu_f": "MCU_F"},
        not_reached_list=[("vcu", "VCU")])
    by = {m["name"]: m for m in res["modules"]}
    assert by["bms"]["counts"]["comm"] == 0
    assert by["vcu"]["counts"]["comm"] == 0
    assert by["mcu_f"]["counts"]["comm"] == 1
    assert res["summary"]["comm"] == 1
    assert res["summary"]["historical"] == 1
