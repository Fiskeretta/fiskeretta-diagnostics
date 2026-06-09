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
    should use when it sends ISO-TP flow-control frames back to the ECU."""
    for cmd in (f"ATSH{request_id:03X}", f"ATCRA{response_id:03X}", f"ATFCSH{request_id:03X}"):
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


async def read_all_dtcs(session: ElmSession, log: Optional[Log] = None) -> dict:
    """Query every known module for its DTCs. A module that doesn't answer
    (unsupported service, no response, etc.) maps to None rather than
    aborting the whole scan."""
    report: dict = {}
    for name, (request_id, response_id) in MODULES.items():
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


async def read_code_detail(session: ElmSession, module: str, code: int) -> dict:
    """On-demand: read one code's freeze-frame and report when/where it occurred."""
    if module not in MODULES:
        return {"available": False}
    request_id, response_id = MODULES[module]
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
