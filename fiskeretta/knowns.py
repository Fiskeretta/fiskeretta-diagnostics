"""
Canonical Fisker Ocean module map — the ECUs Fiskeretta has catalogued from
real cars, baked in so every install scans the full set out of the box and the
"check for extra modules" probe can flag anything genuinely unknown. Built-ins
with friendly labels live in uds.MODULES / modules.FRIENDLY; this is the rest
(addresses + identities found via discovery). Regenerate from a discovery.json.
"""

KNOWN_ECUS = [
    {"request_id": 0x752, "response_id": 0x75A, "module_name": 'ESP', "part_number": 'FM2930200055B'},
    {"request_id": 0x780, "response_id": 0x788, "module_name": 'FCM', "part_number": 'FM2980140002H'},
    {"request_id": 0x782, "response_id": 0x78A, "module_name": 'MRR', "part_number": 'FM2980140080J'},
    {"request_id": 0x783, "response_id": 0x78B, "module_name": 'OHC', "part_number": 'FM2970260042K'},
    {"request_id": 0x785, "response_id": 0x78D, "module_name": None, "part_number": 'FM2970500081G'},
    {"request_id": 0x787, "response_id": 0x78F, "module_name": None, "part_number": 'FM2970260084G'},
    {"request_id": 0x792, "response_id": 0x79A, "module_name": None, "part_number": 'FM2970500060H'},
    {"request_id": 0x793, "response_id": 0x79B, "module_name": 'PSM', "part_number": 'FM2980360121E'},
    {"request_id": 0x794, "response_id": 0x79C, "module_name": 'MRR', "part_number": 'FM2980140080J'},
    {"request_id": 0x796, "response_id": 0x79E, "module_name": 'MRR', "part_number": 'FM2980140080J'},
    {"request_id": 0x797, "response_id": 0x79F, "module_name": None, "part_number": 'FM2940900005E'},
    {"request_id": 0x7A3, "response_id": 0x7AB, "module_name": 'PLGM', "part_number": 'FM2980200160H'},
    {"request_id": 0x7A4, "response_id": 0x7AC, "module_name": 'DSCM', "part_number": 'FM2980360120F'},
    {"request_id": 0x7A6, "response_id": 0x7AE, "module_name": 'ADAS', "part_number": 'FM2980140120H'},
    {"request_id": 0x7A7, "response_id": 0x7AF, "module_name": 'MRR', "part_number": 'FM2980140080J'},
    {"request_id": 0x7B1, "response_id": 0x7B9, "module_name": 'ICC', "part_number": 'FM2970300001Y'},
    {"request_id": 0x7B5, "response_id": 0x7BD, "module_name": 'AMP-SO', "part_number": 'FM2970300083H'},
    {"request_id": 0x7B6, "response_id": 0x7BE, "module_name": 'GW', "part_number": 'FM2980340100L'},
    {"request_id": 0x7D1, "response_id": 0x7D9, "module_name": 'TBOX', "part_number": 'FM2970500100H'},
    {"request_id": 0x7D3, "response_id": 0x7DB, "module_name": 'CIM', "part_number": 'FM2970230001G'},
    {"request_id": 0x7D5, "response_id": 0x7DD, "module_name": 'ESP', "part_number": 'FM2930200064B'},
    {"request_id": 0x7E1, "response_id": 0x7E9, "module_name": 'BMS', "part_number": 'FM2915600180G'},
    {"request_id": 0x7E3, "response_id": 0x7EB, "module_name": 'EPS', "part_number": 'FM2920100001H'},
    {"request_id": 0x7F0, "response_id": 0x7F8, "module_name": 'ECC', "part_number": 'FM2930200180P'},
    {"request_id": 0x7F1, "response_id": 0x7F9, "module_name": None, "part_number": 'VCF1EBU24PG007970'},
    {"request_id": 0x7F2, "response_id": 0x7FA, "module_name": 'MCU_F', "part_number": 'FM2910400041Q'},
    {"request_id": 0x7F3, "response_id": 0x7FB, "module_name": 'PDU', "part_number": 'FM2915840001H'},
    {"request_id": 0x7F4, "response_id": 0x7FC, "module_name": 'iBooster', "part_number": 'FM2920440004J'},
    {"request_id": 0x7F5, "response_id": 0x7FD, "module_name": 'EPS', "part_number": 'FM2920100001H'},
]
