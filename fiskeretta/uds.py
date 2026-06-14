"""
UDS (ISO 14229) over ISO 15765-4 CAN, via ELM327 AT-command framing.

The adapter (protocol 6 = ISO 15765-4 CAN 11-bit @ 500 kbaud, with ATCAF1
"CAN auto formatting" on) handles ISO-TP single/multi-frame plumbing for
us — we just point it at an ECU (Tx header, Rx filter, flow-control header)
and send the UDS service bytes as hex; it hands back the reassembled payload.

Module CAN IDs and the VIN read (service 0x22, DID 0xF190, addressed to the
Gateway at 0x7C2/0x7CA) come from the FOA community's `puddletools/CAN`
(FiskerDBC.dbc) and `puddletools/SaavyScripts` (getVIN.js).
"""

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from . import dtc_catalog
from .core import ElmSession, Log

# Module CAN ID pairs (request -> response), decoded from FiskerDBC.dbc
# (extended IDs with the 0x80000000 "is-extended" flag stripped).
MODULES = {
    "gateway": (0x7C2, 0x7CA),
    "pkc":     (0x7A2, 0x7AA),
    "icc":     (0x781, 0x789),
    "bcm":     (0x7C1, 0x7C9),
    "acu":     (0x7A0, 0x7A8),
    "esp":     (0x7D0, 0x7D8),
    "piu":     (0x791, 0x799),
    "vsp":     (0x7C6, 0x7CE),
    "mcu_f":   (0x786, 0x78E),
}

POSITIVE_RESPONSE_OFFSET = 0x40  # UDS positive response service ID = request + 0x40

# DTC status byte bits (ISO 14229-1 DTCStatusMask). Every ECU carries a large
# table of *trackable* faults — most show up with only the "not completed /
# not failed since last clear" bits (0x10/0x40/0x50) set, meaning that
# particular check has simply never tripped. testFailed (currently failing)
# and confirmedDTC (stored as a real, confirmed fault) are what a driver
# would actually call a "trouble code".
TEST_FAILED = 0x01
CONFIRMED_DTC = 0x08
NOTEWORTHY_STATUS_MASK = TEST_FAILED | CONFIRMED_DTC

_HEX_PAIR = re.compile(r"[0-9A-Fa-f]{2}")
_FRAME_LINE = re.compile(r"^[0-9A-Fa-f]+:\s*(.+)$")
_BARE_HEX = re.compile(r"^[0-9A-Fa-f]+$")
_ADAPTER_ERRORS = ("NO DATA", "ERROR", "UNABLE TO CONNECT", "STOPPED", "BUS INIT", "CAN ERROR")


def _check_adapter_error(raw_reply: str) -> None:
    """Raise a clear UdsError if the adapter answered with a textual status
    (e.g. a sleeping module returning "NO DATA") instead of UDS bytes —
    otherwise stray hex-looking letters in those strings ("DA" in "NO DATA")
    can get misread as payload bytes."""
    upper = raw_reply.upper()
    for err in _ADAPTER_ERRORS:
        if err in upper:
            raise UdsError(f"adapter says: {raw_reply.strip()!r}")


class UdsError(Exception):
    pass


async def configure_addressing(session: ElmSession, request_id: int, response_id: int, log: Optional[Log] = None) -> None:
    """Point the adapter at one ECU: Tx header, Rx filter, and the header it
    should use when it sends ISO-TP flow-control frames back to the ECU.

    11-bit IDs (<= 0x7FF) use a 3-hex header; a request_id above 0xFFF is taken
    to be a 29-bit extended ID (e.g. 0x18DA07F1), where the top byte is the CAN
    priority (set via ATCP) and the low 3 bytes are the header (ATSH/ATFCSH)."""
    if request_id > 0xFFF:  # 29-bit extended diagnostic addressing
        priority = (request_id >> 24) & 0xFF
        cmds = (f"ATCP{priority:02X}", f"ATSH{request_id & 0xFFFFFF:06X}",
                f"ATCRA{response_id:08X}", f"ATFCSH{request_id & 0xFFFFFF:06X}")
    else:
        cmds = (f"ATSH{request_id:03X}", f"ATCRA{response_id:03X}", f"ATFCSH{request_id:03X}")
    for cmd in cmds:
        reply = await session.send(cmd)
        if log:
            log(f"  {cmd:10s} -> {reply!r}")


def _extract_payload_bytes(raw_reply: str) -> bytes:
    """ELM327 (with CAN auto-formatting + long messages) prints multi-frame
    UDS responses like:

        014
        0: 62 F1 90 56 43 46
        1: 31 45 42 55 32 34 50
        2: 47 30 30 37 39 37 30

    — a bare hex byte count ("014" = 0x14 = 20 bytes) followed by one line
    per ISO-TP frame, "<index>: <payload bytes>" with the PCI bytes already
    stripped. We concatenate the frame lines in order and trust the byte
    count to trim any trailing padding. Short single-frame replies have no
    such header/index lines — we fall back to grabbing every hex byte pair.
    """
    lines = [ln.strip() for ln in raw_reply.replace("\r", "\n").split("\n") if ln.strip()]

    frame_lines = []
    declared_len = None
    for ln in lines:
        m = _FRAME_LINE.match(ln)
        if m:
            frame_lines.append(m.group(1))
        elif " " not in ln and _BARE_HEX.match(ln):
            declared_len = int(ln, 16)

    source = frame_lines if frame_lines else lines
    hex_pairs = []
    for ln in source:
        hex_pairs.extend(_HEX_PAIR.findall(ln))

    payload = bytes(int(pair, 16) for pair in hex_pairs)
    if declared_len is not None and declared_len <= len(payload):
        payload = payload[:declared_len]
    return payload


async def read_data_by_identifier(
    session: ElmSession,
    request_id: int,
    response_id: int,
    did: int,
    log: Optional[Log] = None,
) -> bytes:
    """UDS service 0x22 (ReadDataByIdentifier): ask `request_id` for `did`,
    return the data bytes from the positive response (0x62 DID_HI DID_LO <data>)."""
    await configure_addressing(session, request_id, response_id, log)

    request = f"22{did:04X}"
    raw_reply = await session.send(request, timeout=10.0)
    if log:
        log(f"  {request:10s} -> {raw_reply!r}")
    _check_adapter_error(raw_reply)

    payload = _extract_payload_bytes(raw_reply)
    expected_service = 0x22 + POSITIVE_RESPONSE_OFFSET
    if len(payload) < 3 or payload[0] != expected_service:
        raise UdsError(f"unexpected response to ReadDataByIdentifier(0x{did:04X}): {payload.hex()}")

    got_did = (payload[1] << 8) | payload[2]
    if got_did != did:
        raise UdsError(f"DID mismatch: asked for 0x{did:04X}, got 0x{got_did:04X}")

    return bytes(payload[3:])


async def read_vin(session: ElmSession, log: Optional[Log] = None) -> str:
    request_id, response_id = MODULES["gateway"]
    data = await read_data_by_identifier(session, request_id, response_id, 0xF190, log)
    return data.decode("ascii", errors="replace").strip()


@dataclass
class Dtc:
    """A single DTC record: raw 24-bit code + status bitmask.

    Fisker hasn't published a J2012 mapping for these codes — for now we
    report the raw code in hex; cross-reference against community-sourced
    lookup tables once those exist.
    """
    code: int
    status: int

    @property
    def code_hex(self) -> str:
        return f"{self.code:06X}"

    @property
    def is_failing_now(self) -> bool:
        """testFailed bit set — actively failing as of the most recent test."""
        return bool(self.status & TEST_FAILED)

    @property
    def is_confirmed(self) -> bool:
        """confirmedDTC bit set — stored as a real fault. Stays set until the
        DTC memory is cleared, so this includes historical faults that
        aren't necessarily still happening."""
        return bool(self.status & CONFIRMED_DTC)

    @property
    def is_noteworthy(self) -> bool:
        """Currently failing or confirmed — the codes a driver would
        actually care about, vs. the hundreds of "monitor never run"
        entries every ECU's full DTC table carries."""
        return bool(self.status & NOTEWORTHY_STATUS_MASK)

    @property
    def category(self) -> str:
        """First SAE J2012 letter: P/C/B/U (powertrain/chassis/body/network)."""
        return dtc_catalog.j2012(self.code)[:1]

    @property
    def is_comm(self) -> bool:
        """A network (U) code that's confirmed but not currently failing — the
        benign bus-timeout/missing-message noise that re-accumulates on a parked
        car as modules sleep. Hidden by default in the UI; an actively-failing U
        code stays a real fault (is_failing_now → not comm)."""
        return self.category == "U" and self.is_confirmed and not self.is_failing_now

    @property
    def description(self) -> Optional[str]:
        """Human description from the local DTC catalog, if one is installed."""
        return dtc_catalog.describe(self.code)

    def __str__(self) -> str:
        base = f"0x{self.code_hex} (status 0x{self.status:02X})"
        desc = self.description
        return f"{base} — {desc}" if desc else base


async def read_dtcs(
    session: ElmSession,
    request_id: int,
    response_id: int,
    status_mask: int = 0xFF,
    log: Optional[Log] = None,
) -> list["Dtc"]:
    """UDS service 0x19 (ReadDTCInformation), sub-function 0x02
    (reportDTCByStatusMask): ask `request_id` for every DTC matching
    `status_mask` (0xFF = any status) and return the parsed records.

    Positive response: 59 02 <statusAvailabilityMask> [<DTC_hi> <DTC_mid> <DTC_lo> <status>]...
    """
    await configure_addressing(session, request_id, response_id, log)

    request = f"1902{status_mask:02X}"
    raw_reply = await session.send(request, timeout=10.0)
    if log:
        log(f"  {request:10s} -> {raw_reply!r}")
    _check_adapter_error(raw_reply)

    payload = _extract_payload_bytes(raw_reply)
    if len(payload) >= 3 and payload[0] == 0x7F and payload[1] == 0x19:
        raise UdsError(f"negative response (NRC 0x{payload[2]:02X})")

    expected_service = 0x19 + POSITIVE_RESPONSE_OFFSET
    if len(payload) < 3 or payload[0] != expected_service or payload[1] != 0x02:
        raise UdsError(f"unexpected response to ReadDTCInformation: {payload.hex()}")

    records = payload[3:]
    dtcs = []
    for i in range(0, len(records) - 3, 4):
        code = (records[i] << 16) | (records[i + 1] << 8) | records[i + 2]
        dtcs.append(Dtc(code=code, status=records[i + 3]))
    return dtcs


async def read_dtc_extended_data(
    session: ElmSession,
    request_id: int,
    response_id: int,
    dtc_code: int,
    record: int = 0xFF,
    log: Optional[Log] = None,
) -> bytes:
    """UDS service 0x19 sub-function 0x06 (reportDTCExtendedDataRecordByDTCNumber):
    ask one module for the extended data attached to a single DTC. Record 0xFF
    means "all records". ECUs typically store an occurrence counter and aging
    info here, but the per-record layout is manufacturer-defined, so we return
    everything after the status byte as raw bytes for the caller to dump.

    Response: 59 06 <DTC_hi> <DTC_mid> <DTC_lo> <statusOfDTC> [<recNum> <data>...]
    """
    request = f"1906{dtc_code:06X}{record:02X}"
    raw_reply = await session.send(request, timeout=10.0)
    if log:
        log(f"  {request:12s} -> {raw_reply!r}")
    _check_adapter_error(raw_reply)

    payload = _extract_payload_bytes(raw_reply)
    if len(payload) >= 3 and payload[0] == 0x7F and payload[1] == 0x19:
        raise UdsError(f"negative response (NRC 0x{payload[2]:02X})")

    expected_service = 0x19 + POSITIVE_RESPONSE_OFFSET
    if len(payload) < 6 or payload[0] != expected_service or payload[1] != 0x06:
        raise UdsError(f"unexpected response to extended-data read: {payload.hex()}")

    # payload[2:5] echoes the DTC, payload[5] is statusOfDTC; the rest is the
    # extended-data records.
    return bytes(payload[6:])


async def read_dtc_snapshot(
    session: ElmSession,
    request_id: int,
    response_id: int,
    dtc_code: int,
    record: int = 0xFF,
    log: Optional[Log] = None,
) -> bytes:
    """UDS service 0x19 sub-function 0x04 (reportDTCSnapshotRecordByDTCNumber):
    ask one module for the freeze-frame snapshot(s) captured when a DTC set —
    sensor values at the moment of the fault. Record 0xFF means "all records".
    The DID layout is manufacturer-defined, so we return everything after the
    status byte raw.

    Response: 59 04 <DTC_hi> <DTC_mid> <DTC_lo> <statusOfDTC>
              [<recNum> <numIdentifiers> [<DID_hi> <DID_lo> <data>...]...]
    """
    request = f"1904{dtc_code:06X}{record:02X}"
    raw_reply = await session.send(request, timeout=10.0)
    if log:
        log(f"  {request:12s} -> {raw_reply!r}")
    _check_adapter_error(raw_reply)

    payload = _extract_payload_bytes(raw_reply)
    if len(payload) >= 3 and payload[0] == 0x7F and payload[1] == 0x19:
        raise UdsError(f"negative response (NRC 0x{payload[2]:02X})")

    expected_service = 0x19 + POSITIVE_RESPONSE_OFFSET
    if len(payload) < 6 or payload[0] != expected_service or payload[1] != 0x04:
        raise UdsError(f"unexpected response to snapshot read: {payload.hex()}")

    return bytes(payload[6:])


async def read_all_dtcs(session: ElmSession, log: Optional[Log] = None, targets: Optional[dict] = None,
                        progress=None) -> dict:
    """Query each target module for its DTCs. `targets` maps key -> (request_id,
    response_id); defaults to the built-in MODULES. A module that doesn't answer
    maps to None rather than aborting the whole scan. `progress(done, total, name)`
    is called as each module is queried, for a live scan indicator."""
    if targets is None:
        targets = dict(MODULES)
    report: dict = {}
    total = len(targets)
    for idx, (name, (request_id, response_id)) in enumerate(targets.items(), 1):
        if progress:
            progress(idx, total, name)
        if log:
            log(f"Querying {name.upper()} (0x{request_id:03X} -> 0x{response_id:03X}) for DTCs...")
        try:
            dtcs = await read_dtcs(session, request_id, response_id, log=log)
            report[name] = dtcs
            if log:
                for dtc in dtcs:
                    if dtc.is_failing_now:
                        log(f"  {name.upper()}: {dtc}  <-- FAILING NOW")
                    elif dtc.is_confirmed:
                        log(f"  {name.upper()}: {dtc}  <-- confirmed (historical)")
                if dtcs:
                    failing_now = sum(1 for d in dtcs if d.is_failing_now)
                    confirmed = sum(1 for d in dtcs if d.is_confirmed)
                    log(f"  {name.upper()}: {len(dtcs)} DTC(s) in module's table — {failing_now} failing now, {confirmed} confirmed")
                else:
                    log(f"  {name.upper()}: no DTCs stored")
        except (UdsError, asyncio.TimeoutError) as exc:
            report[name] = None
            if log:
                log(f"  {name.upper()}: couldn't read ({exc})")
    return report


async def clear_dtcs(session: ElmSession, request_id: int, response_id: int,
                     log: Optional[Log] = None, attempts: int = 6) -> tuple:
    """UDS 0x14 ClearDiagnosticInformation for all DTC groups (0xFFFFFF) on one
    module. Returns (ok, message); never raises.

    Handles requestCorrectlyReceived-ResponsePending (NRC 0x78) by waiting and
    re-reading — radars/cameras can take seconds to wipe their fault memory and
    reply 0x78 first. Retries transient adapter NO DATA, and falls back to an
    extended diagnostic session once if a real NRC rejects the clear."""
    await configure_addressing(session, request_id, response_id, log)
    tried_extended = False
    saw_pending = False
    last = "no response"
    for _ in range(attempts):
        try:
            reply = await session.send("14FFFFFF", timeout=12.0)
        except asyncio.TimeoutError:
            last = "timeout"
            continue
        up = reply.upper()
        payload = _extract_payload_bytes(reply)
        if payload and payload[0] == 0x54:
            return True, "cleared"
        if len(payload) >= 3 and payload[0] == 0x7F and payload[1] == 0x14:
            nrc = payload[2]
            if nrc == 0x78:                 # responsePending: request accepted, module busy
                saw_pending = True          # this is an ACK, not a rejection
                last = "pending"
                await asyncio.sleep(0.6)
                continue
            if not tried_extended:          # real NRC — try an extended session once
                tried_extended = True
                try:
                    await session.send("1003", timeout=5.0)
                except asyncio.TimeoutError:
                    pass
                last = f"NRC 0x{nrc:02X}"
                continue
            return False, f"rejected (NRC 0x{nrc:02X})"
        if any(err in up for err in _ADAPTER_ERRORS):  # NO DATA / ERROR — often transient
            last = f"adapter: {reply.strip()!r}"
            await asyncio.sleep(0.3)
            continue
        return False, f"unexpected: {payload.hex() if payload else reply.strip()!r}"
    # Ran out of attempts. A module that only ever replied 0x78 DID accept the
    # clear and is still finishing (radars/cameras can take several seconds) —
    # report it as accepted; the caller's re-scan is the final word on success.
    if saw_pending:
        return True, "accepted (clearing)"
    return False, last


# Snapshot DID data lengths (bytes after the 2-byte DID tag), decoded by hand
# from real freeze-frames. 0xEFF6-0xEFF9 are global/environmental fields shared
# across modules; 0x148x are MCU-specific. Lengths verified against captured
# data (records segment with zero leftover bytes).
SNAPSHOT_DID_LEN = {
    0xEFF9: 2, 0xEFF6: 6, 0xEFF8: 4, 0xEFF7: 2,
    0x1485: 4, 0x1484: 5, 0x1486: 1, 0x1487: 8,
    0x1488: 8, 0x1489: 3, 0x148A: 1, 0x148B: 1,
}


def _odometer_miles(raw: bytes) -> float:
    """EFF8 is the odometer in units of 10 m (value / 100 = km), confirmed
    against the car's dash reading."""
    return int.from_bytes(raw, "big") / 100 * 0.621371


def _format_timestamp(raw: bytes) -> str:
    """EFF6 = [year-2015, month(0-indexed), day(0-indexed), hour, minute, second], UTC.

    Epoch (2015), 0-indexed month AND day, and UTC were all confirmed by
    calibrating against a known last-drive instant and odometer: the stored
    UTC value, shifted to local time, matched it exactly. We render UTC plus
    the machine's local time."""
    yy, mo, dd, hh, mi, ss = raw
    try:
        dt = datetime(2015 + yy, mo + 1, dd + 1, hh, mi, ss, tzinfo=timezone.utc)
    except ValueError:
        return f"raw {yy:02d} {mo:02d} {dd:02d} {hh:02d}:{mi:02d}:{ss:02d}"
    local = dt.astimezone()
    return f"{dt:%Y-%m-%d %H:%M:%S} UTC ({local:%Y-%m-%d %H:%M:%S %Z})"


def _format_snapshot(data: bytes) -> list[str]:
    """Pretty-print a freeze-frame payload (everything after 59 04 <DTC> <status>)
    into per-record, per-DID lines, decoding the odometer and timestamp. Falls
    back to a raw dump the moment it meets a DID whose length we don't know."""
    lines: list[str] = []
    i, n = 0, len(data)
    first = True
    while i < n:
        if not first:  # records after the first are zero-padded to a fixed size
            while i < n and data[i] == 0x00:
                i += 1
            if i >= n:
                break
        first = False
        if i + 2 > n:
            break
        rec, nid = data[i], data[i + 1]
        i += 2
        lines.append(f"    record {rec}: {nid} field(s)")
        for _ in range(nid):
            if i + 2 > n:
                lines.append("      (truncated)")
                return lines
            did = (data[i] << 8) | data[i + 1]
            i += 2
            length = SNAPSHOT_DID_LEN.get(did)
            if length is None:
                lines.append(f"      DID 0x{did:04X}: unknown layout — raw tail {data[i:].hex(' ')}")
                return lines
            val = data[i:i + length]
            i += length
            if did == 0xEFF8:
                lines.append(f"      odometer (EFF8): {_odometer_miles(val):,.0f} mi")
            elif did == 0xEFF6:
                ts = "(unset)" if val == b"\x00" * 6 else _format_timestamp(val)
                lines.append(f"      timestamp (EFF6): {ts}")
            else:
                lines.append(f"      DID 0x{did:04X}: {val.hex(' ')}")
    return lines


def parse_snapshot_records(data: bytes) -> list[dict]:
    """Structured variant of _format_snapshot: per freeze-frame record, pull out
    the odometer (EFF8) and timestamp (EFF6) — used to show when/where a code
    occurred. Stops cleanly at any DID whose length we don't know."""
    records: list[dict] = []
    i, n = 0, len(data)
    first = True
    while i < n:
        if not first:
            while i < n and data[i] == 0x00:
                i += 1
            if i >= n:
                break
        first = False
        if i + 2 > n:
            break
        rec, nid = data[i], data[i + 1]
        i += 2
        odometer = None
        timestamp = None
        timestamp_unset = False
        for _ in range(nid):
            if i + 2 > n:
                break
            did = (data[i] << 8) | data[i + 1]
            i += 2
            length = SNAPSHOT_DID_LEN.get(did)
            if length is None:
                break
            val = data[i:i + length]
            i += length
            if did == 0xEFF8:
                odometer = round(_odometer_miles(val))
            elif did == 0xEFF6:
                if val == b"\x00" * 6:
                    timestamp_unset = True
                else:
                    timestamp = _format_timestamp(val)
        records.append({"record": rec, "odometer_mi": odometer,
                        "timestamp": timestamp, "timestamp_unset": timestamp_unset})
    return records


async def read_code_detail(session: ElmSession, request_id: int, response_id: int, code: int) -> dict:
    """On-demand: read one code's freeze-frame and report when/where it occurred."""
    await configure_addressing(session, request_id, response_id)
    try:
        snap = await read_dtc_snapshot(session, request_id, response_id, code)
    except (UdsError, asyncio.TimeoutError):
        return {"available": False}
    records = parse_snapshot_records(snap)
    if not records:
        return {"available": False}
    dated = [r for r in records if r.get("odometer_mi") is not None]
    latest = max(dated, key=lambda r: r["odometer_mi"]) if dated else records[-1]
    return {"available": True, "records": records, "latest": latest}


async def drill_failing_dtcs(session: ElmSession, log: Log) -> None:
    """On a live session: find every currently-failing DTC, then ask its module
    for the extended data (occurrence counter / aging) and freeze-frame
    snapshot — the deepest the car will tell us about a fault over standard UDS."""
    log("Scanning all modules for currently-failing DTCs...")
    report = await read_all_dtcs(session, log=None)
    failing = [
        (name, dtc)
        for name, dtcs in report.items()
        if dtcs
        for dtc in dtcs
        if dtc.is_failing_now
    ]
    if not failing:
        log("No currently-failing DTCs to drill into.")
        return

    log(f"Found {len(failing)} failing DTC(s). Fetching extended data + freeze-frame for each:")
    for name, dtc in failing:
        request_id, response_id = MODULES[name]
        log("")
        log(f"=== {name.upper()}  {dtc} ===")
        await configure_addressing(session, request_id, response_id, log)

        try:
            ext = await read_dtc_extended_data(session, request_id, response_id, dtc.code, log=log)
            log(f"  extended data: {ext.hex(' ') if ext else '(none)'}")
        except (UdsError, asyncio.TimeoutError) as exc:
            log(f"  extended data: couldn't read ({exc})")

        try:
            snap = await read_dtc_snapshot(session, request_id, response_id, dtc.code, log=log)
            if snap:
                log("  freeze-frame:")
                for line in _format_snapshot(snap):
                    log(line)
            else:
                log("  freeze-frame: (none)")
        except (UdsError, asyncio.TimeoutError) as exc:
            log(f"  freeze-frame:  couldn't read ({exc})")


# --- ECU discovery ---------------------------------------------------------

DISCOVERY_START = 0x700
DISCOVERY_END = 0x7FF  # full 0x7xx diagnostic range — a functional 0x7DF probe
                       # revealed modules at 0x7F0–0x7F5, above the old 0x7EF ceiling


async def _identify_ecu(session: ElmSession, request_id: int, response_id: int) -> Optional[str]:
    """Best-effort: read an identification DID to hint at what a discovered ECU is."""
    for did in (0xF197, 0xF187, 0xF18C, 0xF190):
        try:
            data = await read_data_by_identifier(session, request_id, response_id, did)
        except (UdsError, asyncio.TimeoutError):
            continue
        text = "".join(ch for ch in bytes(data).decode("ascii", errors="ignore") if ch.isprintable()).strip()
        if len(text) >= 3:
            return text
    return None


async def _identify_by_dtc(session: ElmSession, request_id: int, response_id: int) -> Optional[str]:
    """Name an ECU from its DTCs: look up which manual section each code lives in
    and take the most common — robust against shared bus/comms codes that show up
    in several modules' sections."""
    try:
        dtcs = await read_dtcs(session, request_id, response_id)
    except (UdsError, asyncio.TimeoutError):
        return None
    tally: dict = {}
    for dtc in dtcs:
        name = dtc_catalog.module_for_code(dtc.code)
        if name:
            tally[name] = tally.get(name, 0) + 1
    if not tally:
        return None
    return max(tally, key=tally.get)


async def discover_ecus(session: ElmSession, log: Optional[Log] = None,
                        start: int = DISCOVERY_START, end: int = DISCOVERY_END,
                        skip_requests: Optional[set] = None) -> list:
    """Probe 11-bit diagnostic request IDs across [start, end] for new ECUs.

    `skip_requests` lists request IDs already known to respond (the permanent
    set — built-ins plus prior discovery); they're not re-probed, so a re-run
    only hunts for genuinely new modules. Returns the new finds (response =
    request + 8), naming each via an identification DID where possible. The
    29-bit pass is a separate call (discover_ecus_29bit)."""
    skip = skip_requests or set()
    found: list = []
    probe_ids = [r for r in range(start, end + 1) if r not in skip]
    total = len(probe_ids)
    if log:
        log(f"Probing {total} unprobed 11-bit IDs (0x{start:03X}-0x{end:03X}, "
            f"skipping {len(skip)} already known); response = request + 8…")

    for offset, req in enumerate(probe_ids):
        resp = req + 8
        await session.send(f"ATSH{req:03X}")
        await session.send(f"ATCRA{resp:03X}")
        try:
            reply = await session.send("3E00", timeout=2.0)
        except asyncio.TimeoutError:
            continue
        if any(err in reply.upper() for err in _ADAPTER_ERRORS):
            continue
        if not _extract_payload_bytes(reply):
            continue

        module_name = await _identify_by_dtc(session, req, resp)
        part_number = await _identify_ecu(session, req, resp)
        entry = {"request_id": req, "response_id": resp, "addressing": "11bit",
                 "module": None, "module_name": module_name, "part_number": part_number,
                 "name_hint": module_name or part_number}
        found.append(entry)
        if log:
            if module_name:
                who = f"NEW — {module_name}" + (f" [{part_number}]" if part_number else "")
            else:
                who = f"NEW — {part_number}" if part_number else "NEW (unidentified)"
            log(f"  0x{req:03X} -> 0x{resp:03X}: {who}")
        if log and offset and offset % 64 == 0:
            log(f"  …{offset}/{total} probed, {len(found)} new so far")

    if log:
        log(f"11-bit sweep complete — {len(found)} new ECU(s) beyond the permanent set.")
    return found


# 29-bit extended diagnostic addressing (ISO 15765-4, protocol 7). Brute-forcing
# 240 physical IDs would be slow, so we instead broadcast functionally on the OBD
# request ID 18DB33F1 and read back every physical responder (18DAF1xx) with
# headers on — one or two messages enumerate the whole bus.
_RESP_29BIT = re.compile(r"18DAF1([0-9A-Fa-f]{2})")


def _parse_29bit_responders(reply: str) -> set:
    """Pull ECU addresses (the xx in 18DAF1xx) out of a headers-on reply."""
    addrs = set()
    for line in reply.replace("\r", "\n").split("\n"):
        compact = re.sub(r"[^0-9A-Fa-f]", "", line)
        for match in _RESP_29BIT.findall(compact):
            addrs.add(int(match, 16))
    return addrs


async def discover_ecus_29bit(session: ElmSession, log: Optional[Log] = None) -> list:
    """Enumerate ECUs on 29-bit extended addressing via a functional broadcast.

    Switches the adapter to protocol 7, sends functional TesterPresent / mode-01
    on 18DB33F1, and collects every physical responder (18DAF1xx). Restores
    protocol 6 (11-bit) before returning so the normal scan path is unaffected.
    Returns entries tagged addressing="29bit" with 29-bit request/response IDs."""
    if log:
        log("Probing 29-bit extended addressing (ISO 15765-4, protocol 7)…")
    found: list = []
    try:
        for cmd in ("ATSP7", "ATCAF1", "ATH1", "ATCP18", "ATSHDB33F1", "ATAR"):
            await session.send(cmd)
        addrs: set = set()
        for probe in ("3E00", "0100"):  # TesterPresent (UDS) + mode-01 (OBD)
            try:
                reply = await session.send(probe, timeout=6.0)
            except asyncio.TimeoutError:
                continue
            if any(err in reply.upper() for err in _ADAPTER_ERRORS):
                continue
            addrs |= _parse_29bit_responders(reply)
        if log:
            listed = ", ".join(f"0x{a:02X}" for a in sorted(addrs)) or "none"
            log(f"  29-bit responders: {listed}")

        for addr in sorted(addrs):
            req = 0x18DA00F1 | (addr << 8)   # 18 DA <addr> F1  (tester -> ECU)
            resp = 0x18DAF100 | addr          # 18 DA F1 <addr>  (ECU -> tester)
            module_name = await _identify_by_dtc(session, req, resp)
            part_number = await _identify_ecu(session, req, resp)
            entry = {"request_id": req, "response_id": resp, "addressing": "29bit",
                     "module": None, "module_name": module_name,
                     "part_number": part_number,
                     "name_hint": module_name or part_number}
            found.append(entry)
            if log:
                who = module_name or part_number or "unidentified"
                log(f"  18DA{addr:02X}F1 -> 18DAF1{addr:02X}: {who}")
    finally:
        for cmd in ("ATH0", "ATSP6", "ATCAF1"):
            try:
                await session.send(cmd)
            except asyncio.TimeoutError:
                pass
    if log:
        log(f"29-bit discovery complete — {len(found)} ECU(s) on extended addressing.")
    return found


# --- Passive bus monitor (sniffing) ----------------------------------------

# With CAN auto-formatting off and headers on, ATMA prints one line per frame:
# "<arb-id> <d0> <d1> …". The arbitration ID is the first token — 3 hex digits
# for an 11-bit ID, 8 for a 29-bit ID.
_ARB_ID = re.compile(r"^(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{8})$")


async def monitor_bus(session: ElmSession, log: Optional[Log] = None, seconds: int = 10) -> list:
    """Passively sniff the bus (ELM327 ATMA) for `seconds`: tally every
    arbitration ID heard and how many frames each sent. Unlike discovery (which
    only finds ECUs that *answer* a diagnostic request), this surfaces any module
    that *transmits* on the bus the adapter sees — so a module that never replies
    to a probe still shows up if its normal traffic reaches the OBD port.

    Returns [{id, id_hex, frames, known}] sorted by frame count. `known` flags
    IDs that match a diagnostic request/response address we already use."""
    if log:
        log(f"Listening to the bus for {seconds}s (passive monitor — ATMA)…")
    # Raw frames (no ISO-TP reassembly) with headers so each line carries its ID,
    # and a fully open acceptance filter (mask 000) — on this adapter ATMA only
    # streams once the mask is opened.
    for cmd in ("ATCAF0", "ATH1", "ATCM000", "ATCF000"):
        await session.send(cmd)
    try:
        raw = await session.monitor("ATMA", seconds)
    finally:
        for cmd in ("ATCRA", "ATH0", "ATCAF1"):  # reset filter + restore normal setup
            try:
                await session.send(cmd)
            except asyncio.TimeoutError:
                pass

    counts: dict = {}
    for line in raw.replace("\r", "\n").split("\n"):
        tokens = line.strip().split()
        if tokens and _ARB_ID.match(tokens[0]):
            arb = tokens[0].upper()
            counts[arb] = counts.get(arb, 0) + 1

    known_ids = set()
    for req, resp in MODULES.values():
        known_ids.add(f"{req:03X}")
        known_ids.add(f"{resp:03X}")

    result = [{"id": int(arb, 16), "id_hex": arb, "frames": n, "known": arb in known_ids}
              for arb, n in counts.items()]
    result.sort(key=lambda r: -r["frames"])

    if log:
        total_frames = sum(r["frames"] for r in result)
        if result:
            new_ids = sum(1 for r in result if not r["known"])
            log(f"Heard {len(result)} distinct arbitration ID(s) ({new_ids} not a known "
                f"diagnostic address) across {total_frames} frames.")
        else:
            # Distinguish a truly silent (gated) bus from a parse miss: show what,
            # if anything, the adapter actually returned.
            sample = " ".join(raw.split())
            if sample:
                log(f"No IDs parsed, but ATMA returned {len(raw)} bytes — format may differ: {sample[:300]}")
            else:
                log("Silent — adapter returned 0 bytes. The OBD port isn't forwarding "
                    "operational traffic (gateway-gated), or the bus is a different speed/protocol.")
    return result


# --- Functional broadcast probe --------------------------------------------

_RESP_11BIT = re.compile(r"^[0-9A-Fa-f]{3}$")


async def functional_probe(session: ElmSession, log: Optional[Log] = None,
                           known_responses: Optional[set] = None) -> list:
    """11-bit functional broadcast on 0x7DF. The address sweep used UDS
    TesterPresent (3E00) and matched only response = request + 8; this asks every
    legislated OBD ECU at once via mode 01/09 *and* TesterPresent, with headers
    on, and captures each responder's actual arbitration ID. It can surface an
    ECU (classically 0x7E0, the powertrain/VCU controller) that ignored the UDS
    sweep but answers OBD modes, or any responder whose ID isn't request + 8.

    Returns [{response_id, request_id, frames, known}]; `known_responses` is the
    set of response IDs we already use, so genuinely new responders are flagged."""
    known = known_responses or {resp for _req, resp in MODULES.values()}
    if log:
        log("Functional broadcast on 0x7DF (OBD mode 01/09 + UDS TesterPresent), headers on…")
    responders: dict = {}
    try:
        # Open the filter to the whole 0x7xx diagnostic range (not just the default
        # 7E8–7EF) so every functional responder is captured on its real ID —
        # including modules outside the legislated OBD block, like the 0x7Fx
        # cluster. The 0x700 mask also blocks ambient broadcasts (e.g. 0x0E9).
        for cmd in ("ATCAF1", "ATH1", "ATSH7DF", "ATCM700", "ATCF700"):
            await session.send(cmd)
        for probe in ("0100", "0900", "3E00"):
            try:
                reply = await session.send(probe, timeout=6.0)
            except asyncio.TimeoutError:
                continue
            for line in reply.replace("\r", "\n").split("\n"):
                tokens = line.strip().split()
                if tokens and _RESP_11BIT.match(tokens[0]):
                    rid = int(tokens[0], 16)
                    responders[rid] = responders.get(rid, 0) + 1
    finally:
        for cmd in ("ATH0", "ATCRA"):  # restore headers off + default filter
            try:
                await session.send(cmd)
            except asyncio.TimeoutError:
                pass

    out = [{"response_id": rid, "request_id": rid - 8, "frames": n, "known": rid in known}
           for rid, n in sorted(responders.items())]
    if log:
        if not out:
            log("  No functional responders — no ECU answered the 0x7DF broadcast.")
        for r in out:
            tag = "known" if r["known"] else "NEW"
            log(f"  responder 0x{r['response_id']:03X} (phys req 0x{r['request_id']:03X}): {tag}")
        new = sum(1 for r in out if not r["known"])
        log(f"Functional probe done — {len(out)} responder(s), {new} new.")
    return out


# --- Generic OBD (SAE J1979) presence probe --------------------------------

# (request, mode, human label). Mode 01 = live data, 09 = vehicle info, 03 =
# stored emissions DTCs — the legislated modes a BEV is not required to expose.
_OBD_PROBES = [
    ("0100", 0x01, "mode 01 PID 00 — supported live-data PIDs"),
    ("0101", 0x01, "mode 01 PID 01 — monitor status / MIL"),
    ("0902", 0x09, "mode 09 PID 02 — VIN"),
    ("03", 0x03, "mode 03 — stored emissions DTCs"),
]


def _obd_answered(reply: str, mode: int) -> bool:
    """True if `reply` carries a positive OBD response for `mode`: the response
    service byte (mode | 0x40) appears near the start of a response frame. We drop
    3-hex CAN IDs and 'N:' frame markers, then look only at the first few bytes of
    each line, so a matching value deep in the data payload (or a CAN ID) can't be
    misread as the service byte."""
    pos = f"{(mode | 0x40):02X}"
    for line in reply.upper().replace("\r", "\n").split("\n"):
        frame_bytes = []
        for tok in line.split():
            if tok.endswith(":") or re.fullmatch(r"[0-9A-F]{3}", tok):
                continue  # frame-index marker ("0:") or a CAN ID / length header
            if re.fullmatch(r"[0-9A-F]{2}", tok):
                frame_bytes.append(tok)
        if pos in frame_bytes[:3]:  # service byte sits in the first 1-3 bytes
            return True
    return False


async def probe_generic_obd(session: ElmSession, log: Optional[Log] = None) -> dict:
    """Ask the legislated OBD-II functional address 0x7DF for standard emissions
    data, to confirm whether the Ocean exposes any generic OBD or is UDS-only.
    Returns {"generic_obd": bool, "probes": [{request, label, answered, reply}]}.
    """
    if log:
        log("Generic-OBD probe on 0x7DF (SAE J1979 modes 01 / 09 / 03)…")
    results = []
    try:
        # Same broadcast setup as the functional ECU probe: headers on, Tx 0x7DF,
        # filter open to the whole 0x7xx range (also blocks ambient 0x0E9).
        for cmd in ("ATCAF1", "ATH1", "ATSH7DF", "ATCM700", "ATCF700"):
            await session.send(cmd)
        for req, mode, label in _OBD_PROBES:
            try:
                reply = await session.send(req, timeout=6.0)
            except asyncio.TimeoutError:
                reply = "(timeout)"
            answered = _obd_answered(reply, mode)
            if log:
                log(f"  {req:5s} -> {'answered' if answered else 'no answer'}: {reply.strip()!r}")
            results.append({"request": req, "label": label,
                            "answered": answered, "reply": reply.strip()})
    finally:
        for cmd in ("ATH0", "ATCRA"):  # restore headers off + default filter
            try:
                await session.send(cmd)
            except asyncio.TimeoutError:
                pass
    generic = any(r["answered"] for r in results)
    if log:
        log("Generic OBD present — the car answered a legislated mode."
            if generic else "No generic OBD — the Ocean is UDS-only (as expected).")
    return {"generic_obd": generic, "probes": results}


# --- BMS / live-data DID sweep ---------------------------------------------

async def sweep_dids(session: ElmSession, request_id: int, response_id: int,
                     dids, log: Optional[Log] = None, progress=None) -> list:
    """Sweep UDS 0x22 (ReadDataByIdentifier) across `dids` on one module, returning
    [{did, length, raw}] for every DID that gives a positive (0x62) response.

    Addressing is configured once up front, then bare 22xxxx requests are sent in
    a loop. Adapter-error strings, timeouts, and negative responses (0x7F) are
    skipped. Cancellation (asyncio) propagates out cleanly between requests."""
    await configure_addressing(session, request_id, response_id, log)
    dids = list(dids)
    total = len(dids)
    found = []
    for i, did in enumerate(dids):
        if not 0 <= did <= 0xFFFF:  # a DID is a 2-byte identifier; anything else
            if progress:           # would build a malformed 22xxxx request
                progress(i + 1, total, did)
            continue
        try:
            reply = await session.send(f"22{did:04X}", timeout=5.0)
        except asyncio.TimeoutError:
            if progress:
                progress(i + 1, total, did)
            continue
        try:
            _check_adapter_error(reply)
        except UdsError:
            if progress:
                progress(i + 1, total, did)
            continue
        payload = _extract_payload_bytes(reply)
        if (len(payload) >= 3 and payload[0] == 0x22 + POSITIVE_RESPONSE_OFFSET
                and payload[1] == (did >> 8) and payload[2] == (did & 0xFF)):
            data = payload[3:]
            found.append({"did": f"{did:04X}", "length": len(data),
                          "raw": data.hex().upper()})
            if log:
                log(f"  DID {did:04X}: {len(data)} byte(s) = {data.hex().upper()}")
        if progress:
            progress(i + 1, total, did)
    if log:
        log(f"DID sweep done — {len(found)} of {total} DIDs responded.")
    return found


# --- Deep identification (fingerprint & compare ECUs) ----------------------

# ISO 14229 identification DIDs (0xF1xx) worth fingerprinting an ECU by. The
# part number (F187) and system name (F197) are the tell: front/rear inverters
# are identical hardware, so they share a part-number family.
_IDENT_DIDS = [
    (0xF197, "system name/type"),
    (0xF187, "spare part no"),
    (0xF191, "hardware no"),
    (0xF188, "software no"),
    (0xF18A, "supplier id"),
    (0xF18C, "serial no"),
    (0xF190, "VIN"),
]


def _decode_ascii(data: bytes) -> str:
    return "".join(ch for ch in data.decode("ascii", errors="ignore") if ch.isprintable()).strip()


async def deep_identify(session: ElmSession, log: Optional[Log] = None,
                        targets: Optional[list] = None) -> list:
    """Dump identification DIDs from a set of ECUs so they can be fingerprinted
    and compared side by side. `targets` is a list of (label, request_id,
    response_id); defaults to MCU_F (the known front inverter) plus nothing — the
    caller normally passes MCU_F as a reference alongside the unidentified
    responders, to test whether one of the unknowns is the rear inverter or VCU
    (same part-number family as MCU_F).

    Returns [{label, request_id, response_id, dids:{DID:{ascii,hex}}}]."""
    if targets is None:
        targets = [("MCU_F (reference)", *MODULES["mcu_f"])]
    results = []
    for label, req, resp in targets:
        if log:
            log(f"— {label}  (0x{req:03X} -> 0x{resp:03X})")
        dids: dict = {}
        for did, name in _IDENT_DIDS:
            try:
                data = bytes(await read_data_by_identifier(session, req, resp, did))
            except (UdsError, asyncio.TimeoutError):
                continue
            text = _decode_ascii(data)
            dids[f"{did:04X}"] = {"ascii": text, "hex": data.hex()}
            if log:
                log(f"    {did:04X} {name:16s}: {text or data.hex()}")
        if not dids and log:
            log("    (no identification DIDs answered)")
        results.append({"label": label, "request_id": req, "response_id": resp, "dids": dids})
    return results


# --- No-filter sweep (catch off-pattern responders) ------------------------

async def no_filter_sweep(session: ElmSession, log: Optional[Log] = None,
                          start: int = DISCOVERY_START, end: int = DISCOVERY_END,
                          skip_requests: Optional[set] = None) -> list:
    """Re-probe 11-bit request IDs accepting any response in the diagnostic range
    (0x7xx), headers on, so a module replying on an ID *other than* request+8 is
    still captured — the normal sweep's exact ATCRA filter would silently drop it.

    The hardware mask is set to 0x700/0x700, not fully open: that admits every
    0x7xx diagnostic reply but blocks ambient broadcast traffic (e.g. the gateway's
    0x0E9 heartbeat in Drive). A fully open filter turns each probe into an
    unstoppable stream of that broadcast, which starves the event loop and hangs
    the app — so we filter it out at the adapter. Raw framing (CAF off) so each
    responder's arbitration ID is visible.

    Returns [{request_id, response_id, off_pattern, frame}]."""
    skip = skip_requests or set()
    probe_ids = [r for r in range(start, end + 1) if r not in skip]
    found: list = []
    if log:
        log(f"No-filter sweep — re-probing {len(probe_ids)} IDs, accepting any 0x7xx "
            f"responder (headers on). Catches replies that aren't request+8…")
    seen: set = set()
    try:
        # ATCM700/ATCF700: pass IDs where (id & 0x700) == 0x700, i.e. 0x700–0x7FF
        # only — diagnostic replies get through, ambient broadcasts are dropped.
        for cmd in ("ATCAF0", "ATH1", "ATCM700", "ATCF700"):
            await session.send(cmd)
        for offset, req in enumerate(probe_ids):
            await session.send(f"ATSH{req:03X}")
            try:
                reply = await session.send("023E00", timeout=1.5)  # raw single-frame TesterPresent
            except asyncio.TimeoutError:
                continue
            if any(err in reply.upper() for err in _ADAPTER_ERRORS):
                continue
            for line in reply.replace("\r", "\n").split("\n"):
                tokens = line.strip().split()
                if not tokens or not _RESP_11BIT.match(tokens[0]):
                    continue
                rid = int(tokens[0], 16)
                # Diagnostic responses live in 0x7xx; anything lower (e.g. 0x0E9)
                # is ambient broadcast leaking through the open filter — ignore it.
                if rid < 0x700:
                    continue
                if (req, rid) in seen:
                    continue
                seen.add((req, rid))
                entry = {"request_id": req, "response_id": rid,
                         "off_pattern": rid != req + 8, "frame": " ".join(tokens)}
                found.append(entry)
                if log:
                    flag = "   ← NOT +8!" if entry["off_pattern"] else ""
                    log(f"  0x{req:03X} → responder 0x{rid:03X}{flag}")
            if log and offset and offset % 64 == 0:
                log(f"  …{offset}/{len(probe_ids)} probed, {len(found)} responder(s) so far")
    finally:
        for cmd in ("ATCAF1", "ATH0", "ATCRA"):  # restore normal framing/filter
            try:
                await session.send(cmd)
            except asyncio.TimeoutError:
                pass
    if log:
        off = sum(1 for f in found if f["off_pattern"])
        log(f"No-filter sweep done — {len(found)} responder(s), {off} on a non-+8 ID.")
    return found
