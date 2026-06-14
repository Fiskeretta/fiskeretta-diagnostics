"""Phase 5 — BMS DID sweep + signal decoding (no hardware; fake ELM session)."""
import asyncio

from fiskeretta import bms_signals, uds


class FakeSession:
    """Stands in for an ElmSession: returns a canned reply per command."""
    def __init__(self, replies):
        self.replies = replies
        self.sent = []

    async def send(self, command, timeout=5.0):
        self.sent.append(command)
        return self.replies.get(command, "")


def test_sweep_collects_only_positive_responses():
    replies = {
        "22F190": "62 F1 90 56 43 46 31",   # positive, 4 data bytes
        "22F191": "7F 22 31",               # negative response -> skip
        "22F192": "NO DATA",                # adapter/no-data -> skip
    }
    s = FakeSession(replies)
    rows = asyncio.run(uds.sweep_dids(s, 0x7E1, 0x7E9, [0xF190, 0xF191, 0xF192]))
    assert len(rows) == 1
    assert rows[0] == {"did": "F190", "length": 4, "raw": "56434631"}
    # addressing was configured up front
    assert "ATSH7E1" in s.sent


def test_sweep_rejects_did_echo_mismatch():
    # positive service byte but the wrong DID echoed back -> not a match
    s = FakeSession({"22F190": "62 F1 91 00 00"})
    rows = asyncio.run(uds.sweep_dids(s, 0x7E1, 0x7E9, [0xF190]))
    assert rows == []


def test_sweep_progress_reports_each_did():
    calls = []
    s = FakeSession({})
    asyncio.run(uds.sweep_dids(s, 0x7E1, 0x7E9, [0x0001, 0x0002],
                               progress=lambda done, total, did: calls.append((done, total))))
    assert calls == [(1, 2), (2, 2)]


def test_sweep_skips_out_of_range_dids():
    # 0xFFFF is the largest valid DID; 0x10000 must be skipped (not sent), else it
    # would build a malformed 5-hex 22xxxxx request.
    s = FakeSession({"22FFFF": "62 FF FF 01"})
    rows = asyncio.run(uds.sweep_dids(s, 0x7E1, 0x7E9, [0xFFFF, 0x10000]))
    assert [r["did"] for r in rows] == ["FFFF"]
    assert not any(c.startswith("2210000") for c in s.sent)


def test_decode_known_identification_did():
    d = bms_signals.decode(0xF190, b"VCF1EBU24PG007970")
    assert d == {"name": "VIN", "value": "VCF1EBU24PG007970", "unit": None}


def test_decode_unknown_did_returns_none():
    assert bms_signals.decode(0x1234, b"\x01\x02") is None


def test_decode_hex_wrapper():
    assert bms_signals.decode_hex("F190", "564346")["value"] == "VCF"
    assert bms_signals.decode_hex("9999", "00") is None


def test_battery_map_empty_no_guessed_scalings():
    # guard: no battery DID scalings should be committed until calibrated
    assert bms_signals.BATTERY == {}
