"""Registry merge precedence: the curated baked map (knowns.KNOWN_ECUS) is
authoritative for the addresses it defines; runtime discovery only ADDS
addresses the baked map lacks, never relabels one it already curates."""
from fiskeretta import registry, storage


def test_baked_map_wins_over_stale_runtime_label(monkeypatch):
    fake = {"ecus": [
        # Stale heuristic label for an address the baked map curates (0x782 = CMRR_FR).
        {"request_id": 0x782, "response_id": 0x78A, "addressing": "11bit",
         "module": None, "module_name": "MRR", "part_number": "FM2980140080J"},
        # An address the baked map does NOT have — should still be added.
        {"request_id": 0x701, "response_id": 0x709, "addressing": "11bit",
         "module": None, "module_name": "NEWBIE", "part_number": None},
    ]}
    monkeypatch.setattr(storage, "load_discovery", lambda: fake)
    targets = registry.scan_targets()

    by_req = {t["request"]: t for t in targets.values()}
    assert by_req[0x782]["label"] == "Corner Radar, Front Right"   # baked wins
    assert 0x701 in by_req                                          # runtime-only added
    assert by_req[0x701]["label"] == "NEWBIE"


def test_corrected_radar_and_additions_present(monkeypatch):
    monkeypatch.setattr(storage, "load_discovery", lambda: None)  # fresh install
    by_req = {t["request"]: t for t in registry.scan_targets().values()}
    assert by_req[0x781]["label"] == "Mid-Range Radar"            # was mislabeled "icc"
    assert by_req[0x7B1]["label"] == "Infotainment Controller"    # the real ICC
    assert by_req[0x7A6]["label"] == "ADAS Domain Controller (Hydra3)"
    assert by_req[0x742]["label"] == "Telematics Control Unit (US)"  # new address
    assert by_req[0x7C7]["label"] == "Thermal Regulation Module"      # new address (TRM)
    assert by_req[0x752]["label"] == "WTC"  # acronym kept (expansion unverified)
