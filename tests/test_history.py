"""Phase 3 — history helpers (diff.py).

Synthetic scans modelled on the real clear -> re-accumulate pattern: clearing a
parked car wipes everything, then ~comm codes flood back as historical noise.
"""
from fiskeretta import diff


def scan(when, codes):
    """codes: list of (module, code, j2012, failing_now, tier)."""
    mods = {}
    for m, code, j, fn, tier in codes:
        mods.setdefault(m, []).append(
            {"code": code, "j2012": j, "failing_now": fn, "tier": tier, "status_byte": 0})
    return {"scanned_at": when,
            "modules": [{"name": m, "codes": cs} for m, cs in mods.items()]}


POST_CLEAR = scan("2026-06-14T05:28:10Z", [
    ("mcu_f", "0xD14987", "U114987", True, "fault"),
    ("pdu", "0x118700", "P118700", True, "fault"),
    ("tbox", "0x17A100", "U17A100", True, "fault"),   # active, gone next scan
])
LATER = scan("2026-06-14T05:45:01Z", [
    ("mcu_f", "0xD14987", "U114987", True, "fault"),   # unchanged
    ("pdu", "0x118700", "P118700", True, "fault"),     # unchanged
    ("bcm", "0xA21401", "B221401", False, "fault"),    # appeared, real historical
    ("mcu_f", "0xD14281", "U114281", False, "comm"),   # appeared comm
    ("mcu_r", "0xD14281", "U114281", False, "comm"),   # appeared comm
])


def test_diff_splits_comm_from_real_faults():
    d = diff.diff_scans(POST_CLEAR, LATER)
    appeared = {r["code"] for r in d["appeared"]}
    assert appeared == {"0xA21401"}              # only the real historical fault
    assert all(r["tier"] != "comm" for r in d["appeared"])
    assert d["comm_appeared"] == 2               # the two U comm codes collapsed
    resolved = {r["code"] for r in d["resolved"]}
    assert resolved == {"0x17A100"}              # tbox cleared/asleep
    assert d["comm_resolved"] == 0


def test_diff_no_previous_treats_all_as_appeared():
    d = diff.diff_scans(None, LATER)
    # every non-comm code in LATER counts as appeared; comm collapses to a count
    assert {r["code"] for r in d["appeared"]} == {"0xD14987", "0x118700", "0xA21401"}
    assert d["comm_appeared"] == 2
    assert d["resolved"] == [] and d["comm_resolved"] == 0


def test_code_history_newest_first():
    hist = diff.code_history([POST_CLEAR, LATER], "mcu_f", "0xD14987")
    assert len(hist) == 2
    assert hist[0]["when"] > hist[1]["when"]
    # a code only in LATER appears once
    assert len(diff.code_history([POST_CLEAR, LATER], "bcm", "0xA21401")) == 1


def test_recurrence_summary():
    recs = [{"odometer_mi": 10470}, {"odometer_mi": 14072}, {"odometer_mi": None}]
    s = diff.recurrence_summary(recs)
    assert s == {"count": 3, "first_mi": 10470, "latest_mi": 14072, "recurring": True}
    assert diff.recurrence_summary([{"odometer_mi": 100}])["recurring"] is False
    assert diff.recurrence_summary([])["count"] == 0


def test_record_and_list_events_roundtrip(tmp_path, monkeypatch):
    from fiskeretta import storage
    monkeypatch.setattr(storage, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(storage, "EVENTS_FILE", tmp_path / "events.jsonl")

    assert storage.list_events() == []
    storage.record_clear({"active": 4, "historical": 26, "comm": 25}, ["bcm", "mcu_f"])
    storage.record_clear({"active": 0, "historical": 0, "comm": 0}, ["bms"])

    evs = storage.list_events()
    assert len(evs) == 2
    assert evs[0]["type"] == "clear"
    assert evs[0]["wiped"] == {"active": 4, "historical": 26, "comm": 25}
    assert evs[0]["modules"] == ["bcm", "mcu_f"]

    # a None summary records zeros, and a corrupt line is skipped, not fatal
    storage.record_clear(None, None)
    with (tmp_path / "events.jsonl").open("a", encoding="utf-8") as f:
        f.write("not json\n")
    evs = storage.list_events()
    assert len(evs) == 3
    assert evs[2]["wiped"] == {"active": 0, "historical": 0, "comm": 0}


def test_merge_history_orders_newest_first_and_tags_kinds():
    scans = [
        {"scanned_at": "2026-06-14T05:45:01Z", "id": "s2", "active": 3, "historical": 5, "comm": 25},
        {"scanned_at": "2026-06-14T05:27:00Z", "id": "s1", "active": 4, "historical": 0, "comm": 0},
    ]
    events = [{"when": "2026-06-14T05:28:10Z", "type": "clear",
               "wiped": {"active": 4, "historical": 26, "comm": 25}}]
    merged = diff.merge_history(scans, events)
    assert [m["kind"] for m in merged] == ["scan", "clear", "scan"]
    assert merged[1]["wiped"]["historical"] == 26
