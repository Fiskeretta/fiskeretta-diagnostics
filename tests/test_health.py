"""Phase 6 — battery health verdict thresholds (boundary cases)."""
from fiskeretta import health


def lvl(signals):
    return health.verdict(signals)["level"]


def test_no_signals_is_unknown():
    assert health.verdict({})["level"] == "unknown"
    assert health.verdict(None)["level"] == "unknown"


def test_cell_imbalance_boundaries():
    assert lvl({"cell_imbalance_mv": 79}) == "ok"
    assert lvl({"cell_imbalance_mv": 80}) == "warn"      # >=80 mV early warning
    assert lvl({"cell_imbalance_mv": 149}) == "warn"
    assert lvl({"cell_imbalance_mv": 150}) == "alert"


def test_isolation_boundaries():
    assert lvl({"isolation_ohm_per_v": 1200}) == "ok"
    assert lvl({"isolation_ohm_per_v": 999}) == "warn"
    assert lvl({"isolation_ohm_per_v": 500}) == "warn"
    assert lvl({"isolation_ohm_per_v": 499}) == "alert"  # below 500 floor


def test_pack_temp_bands():
    assert lvl({"pack_temp_c": 30}) == "ok"
    assert lvl({"pack_temp_c": 45}) == "warn"
    assert lvl({"pack_temp_c": 55}) == "alert"
    assert lvl({"pack_temp_c": -25}) == "warn"


def test_aux_12v_bands():
    assert lvl({"aux_12v": 12.6}) == "ok"
    assert lvl({"aux_12v": 11.7}) == "warn"
    assert lvl({"aux_12v": 10.9}) == "alert"


def test_dcdc_range():
    assert lvl({"dcdc_v": 14.0}) == "ok"
    assert lvl({"dcdc_v": 11.9}) == "warn"
    assert lvl({"dcdc_v": 15.6}) == "warn"


def test_overall_level_is_worst_of_signals():
    v = health.verdict({"cell_imbalance_mv": 10, "isolation_ohm_per_v": 499, "aux_12v": 12.6})
    assert v["level"] == "alert"
    assert len(v["reasons"]) == 3
    # each reason carries its own grade and message
    by = {r["signal"]: r for r in v["reasons"]}
    assert by["isolation_ohm_per_v"]["level"] == "alert"
    assert by["cell_imbalance_mv"]["level"] == "ok"
    assert "mV" in by["cell_imbalance_mv"]["message"]
