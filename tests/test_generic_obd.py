"""Phase 4 — generic-OBD presence parser.

_obd_answered must recognise a positive OBD response (service byte = mode|0x40)
while ignoring 3-hex CAN IDs and rejecting NO DATA / negative responses.
"""
from fiskeretta.uds import _obd_answered


def test_positive_mode01_headers_on():
    assert _obd_answered("7E8 06 41 00 BE 3F A8 13", 0x01) is True


def test_positive_mode01_headers_off():
    assert _obd_answered("41 00 BE 3F A8 13", 0x01) is True


def test_positive_mode09_vin_multiframe():
    reply = "0: 49 02 01 56 43 46\n1: 31 45 42 55 32 34 50"
    assert _obd_answered(reply, 0x09) is True


def test_positive_mode03():
    assert _obd_answered("43 00", 0x03) is True


def test_no_data_is_negative():
    assert _obd_answered("NO DATA", 0x01) is False
    assert _obd_answered("", 0x01) is False
    assert _obd_answered("(timeout)", 0x01) is False


def test_uds_negative_response_not_counted():
    # 7F 01 11 = serviceNotSupported for mode 01 — no 0x41 byte present
    assert _obd_answered("7F 01 11", 0x01) is False


def test_can_id_not_misread_as_service_byte():
    # A bare responder ID line with no payload must not register as a 0x41 answer
    assert _obd_answered("741", 0x01) is False
    # but a real 41 byte alongside an ID still counts
    assert _obd_answered("7E8 03 41 00 01", 0x01) is True


def test_wrong_mode_byte_not_matched():
    # a mode 01 positive reply should not satisfy a mode 09 probe
    assert _obd_answered("41 00 BE 3F A8 13", 0x09) is False


def test_matching_byte_deep_in_payload_not_misread():
    # 0x43 appears as a data byte, not the service byte -> not a mode-03 answer
    assert _obd_answered("7E8 06 41 00 BE 3F 43 13", 0x03) is False
    # but a real mode-03 service byte at the front still counts
    assert _obd_answered("7E8 03 43 01 00", 0x03) is True
