"""Safe-tier routines: ECU reset (UDS 0x11) + read-identification (0x22)."""
import asyncio

from fiskeretta import registry, server, uds


def _ascii_resp(service_did_prefix, text):
    body = " ".join(f"{ord(c):02X}" for c in text)
    return f"{service_did_prefix} {body}"


class FakeSession:
    """Minimal ElmSession stand-in: AT* -> 'OK', else look up a scripted reply
    (a list is consumed in order; a bare string repeats)."""
    def __init__(self, script):
        self.script = dict(script)
        self.sent = []

    async def send(self, command, timeout=5.0):
        self.sent.append(command)
        if command.upper().startswith("AT"):
            return "OK"
        v = self.script.get(command)
        if isinstance(v, list):
            return v.pop(0) if v else "NO DATA"
        return v if v is not None else "NO DATA"


def test_ecu_reset_soft_and_hard_ok():
    s = FakeSession({"1103": "51 03"})
    ok, msg = asyncio.run(uds.ecu_reset(s, 0x7C2, 0x7CA, hard=False))
    assert ok is True and "1103" in s.sent

    s = FakeSession({"1101": "51 01"})
    ok, _ = asyncio.run(uds.ecu_reset(s, 0x786, 0x78E, hard=True))
    assert ok is True and "1101" in s.sent


def test_ecu_reset_rejected_tries_extended_session_once():
    s = FakeSession({"1103": ["7F 11 22", "7F 11 22"], "1003": "50 03"})
    ok, msg = asyncio.run(uds.ecu_reset(s, 0x7C1, 0x7C9))
    assert ok is False and "NRC 0x22" in msg
    assert "1003" in s.sent  # escalated to an extended session before giving up


def test_ecu_reset_pending_then_ok():
    s = FakeSession({"1103": ["7F 11 78", "51 03"]})  # 0x78 = responsePending (ACK)
    ok, _ = asyncio.run(uds.ecu_reset(s, 0x7E1, 0x7E9))
    assert ok is True


def test_read_identification_collects_answered_dids():
    s = FakeSession({
        "22F190": _ascii_resp("62 F1 90", "VCF1EBU24PG007970"),
        "22F187": _ascii_resp("62 F1 87", "FM2915600180G"),
        # F188/F191/F18A/F18C unscripted -> 'NO DATA' -> omitted
    })
    info = asyncio.run(uds.read_identification(s, 0x7E1, 0x7E9))
    assert info["vin"] == "VCF1EBU24PG007970"
    assert info["part_number"] == "FM2915600180G"
    assert "software" not in info


def test_recovery_flow_keys_are_real_scan_targets():
    targets = registry.scan_targets()
    for flow_key, (label, steps) in server.RECOVERY_FLOWS.items():
        for key, hard in steps:
            assert key in targets, f"recovery flow '{flow_key}' references unknown module '{key}'"
