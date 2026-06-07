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

import re
from typing import Optional

from bleak import BleakClient
from bleak.backends.device import BLEDevice

from . import core
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

_HEX_PAIR = re.compile(r"[0-9A-Fa-f]{2}")
_FRAME_LINE = re.compile(r"^[0-9A-Fa-f]+:\s*(.+)$")
_BARE_HEX = re.compile(r"^[0-9A-Fa-f]+$")


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


async def read_vin_from_device(device: BLEDevice, log: Log) -> Optional[str]:
    """Connect, run the minimal AT setup, and read the VIN — a single
    end-to-end smoke test that we can talk real UDS to the car's ECUs."""
    log(f"Connecting to {device.name} ({device.address})...")
    async with BleakClient(device) as client:
        write_char, notify_char = core.find_uart_pair(client)
        if not write_char or not notify_char:
            log("Couldn't auto-detect a write+notify characteristic pair.")
            return None

        async with ElmSession(client, write_char, notify_char) as session:
            log("Setting up adapter (reset, echo off, force ISO 15765-4 CAN, auto-format, allow long messages)...")
            for cmd in ("ATZ", "ATE0", "ATSP6", "ATCAF1", "ATAL"):
                reply = await session.send(cmd)
                log(f"  {cmd:10s} -> {reply!r}")

            log("Reading VIN from the Gateway module (UDS 0x22, DID 0xF190)...")
            try:
                vin = await read_vin(session, log)
            except UdsError as exc:
                log(f"Failed to read VIN: {exc}")
                return None

            log(f"VIN: {vin}")
            return vin
