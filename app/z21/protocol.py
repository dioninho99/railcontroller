"""
RailController – Z21 LAN Protocol
Basiert auf: Roco Z21 LAN Protocol Specification V1.13
UDP Port: 21105
"""

import struct

Z21_UDP_PORT = 21105

HEADER_LAN_GET_SERIAL_NUMBER    = 0x10
HEADER_LAN_GET_HW_INFO          = 0x1A
HEADER_LAN_LOGOFF               = 0x30
HEADER_LAN_X_HEADER             = 0x40
HEADER_LAN_SET_BROADCASTFLAGS   = 0x50
HEADER_LAN_GET_BROADCASTFLAGS   = 0x51
HEADER_LAN_SYSTEMSTATE_GETDATA  = 0x85

BROADCAST_DRIVING_SWITCHING     = 0x00000001
BROADCAST_SYSTEMSTATE_CHANGES   = 0x00000100

SPEED_STEPS_14  = 0
SPEED_STEPS_28  = 2
SPEED_STEPS_128 = 4

XHEADER_GET_STATUS          = 0x21
XHEADER_SET_TRACK_POWER_OFF = 0x21
XHEADER_SET_TRACK_POWER_ON  = 0x21
XHEADER_LOCO_DRIVE          = 0xE4
XHEADER_LOCO_FUNCTION       = 0xF8
XHEADER_GET_LOCO_INFO       = 0xE3
XHEADER_LOCO_INFO           = 0xEF
XHEADER_SET_TURNOUT         = 0x53
XHEADER_GET_TURNOUT_INFO    = 0x43
XHEADER_SET_STOP            = 0x80


def _build_packet(header: int, data: bytes = b"") -> bytes:
    length = 4 + len(data)
    return struct.pack("<HH", length, header) + data


def _xor(data: bytes) -> int:
    r = 0
    for b in data:
        r ^= b
    return r


def _xbus(xheader: int, db: bytes) -> bytes:
    xor = _xor(bytes([xheader]) + db)
    return _build_packet(HEADER_LAN_X_HEADER, bytes([xheader]) + db + bytes([xor]))


# ── Builder ────────────────────────────────────

def build_get_serial_number() -> bytes:
    return _build_packet(HEADER_LAN_GET_SERIAL_NUMBER)

def build_logoff() -> bytes:
    return _build_packet(HEADER_LAN_LOGOFF)

def build_get_status() -> bytes:
    return _xbus(0x21, bytes([0x24]))

def build_track_power_on() -> bytes:
    return _xbus(0x21, bytes([0x81]))

def build_track_power_off() -> bytes:
    return _xbus(0x21, bytes([0x80]))

def build_emergency_stop() -> bytes:
    return _xbus(0x80, b"")

def build_set_broadcast_flags(flags: int) -> bytes:
    return _build_packet(HEADER_LAN_SET_BROADCASTFLAGS, struct.pack("<I", flags))

def build_systemstate_get() -> bytes:
    return _build_packet(HEADER_LAN_SYSTEMSTATE_GETDATA)

def build_get_loco_info(address: int) -> bytes:
    hi = (address >> 8) | 0xC0
    lo = address & 0xFF
    return _xbus(0xE3, bytes([0x05, hi, lo]))

def build_loco_drive(address: int, speed: int, forward: bool,
                     speed_steps: int = SPEED_STEPS_128) -> bytes:
    hi = (address >> 8) | 0xC0
    lo = address & 0xFF
    db0 = {SPEED_STEPS_128: 0x13, SPEED_STEPS_28: 0x12, SPEED_STEPS_14: 0x10}.get(speed_steps, 0x13)
    speed_byte = (speed & 0x7F) | (0x80 if forward else 0x00)
    return _xbus(0xE4, bytes([db0, hi, lo, speed_byte]))

def build_loco_function(address: int, function: int, state: bool) -> bytes:
    """
    F0–F28 schalten.
    Gruppe 0: F0–F4   (db0=0x20 | bits)
    Gruppe 1: F5–F8   (db0=0x21 | bits)
    Gruppe 2: F9–F12  (db0=0x22 | bits)
    Gruppe 3: F13–F20 (db0=0x23 | bits)
    Gruppe 4: F21–F28 (db0=0x28 | bits)
    """
    hi = (address >> 8) | 0xC0
    lo = address & 0xFF

    if function == 0:
        db0 = 0x20
        func_byte = (0x10 if state else 0x00)
    elif 1 <= function <= 4:
        db0 = 0x20
        func_byte = (1 << (function - 1)) if state else 0x00
    elif 5 <= function <= 8:
        db0 = 0x21
        func_byte = (1 << (function - 5)) if state else 0x00
    elif 9 <= function <= 12:
        db0 = 0x22
        func_byte = (1 << (function - 9)) if state else 0x00
    elif 13 <= function <= 20:
        db0 = 0x23
        func_byte = (1 << (function - 13)) if state else 0x00
    elif 21 <= function <= 28:
        db0 = 0x28
        func_byte = (1 << (function - 21)) if state else 0x00
    else:
        return b""

    return _xbus(0xF8, bytes([db0, hi, lo, func_byte]))

def build_set_turnout(address: int, thrown: bool, activate: bool = True) -> bytes:
    """
    thrown=False → gerade (output1, bit0=0)
    thrown=True  → abbiegen (output2, bit0=1)
    activate=True  → Spule AN  (bit3=1)
    activate=False → Spule AUS (bit3=0)
    """
    addr = address - 1
    hi = (addr >> 8) & 0xFF
    lo = addr & 0xFF
    nibble = (0x08 if activate else 0x00) | (0x01 if thrown else 0x00)
    return _xbus(0x53, bytes([hi, lo, nibble]))

def build_get_turnout_info(address: int) -> bytes:
    addr = address - 1
    hi = (addr >> 8) & 0xFF
    lo = addr & 0xFF
    return _xbus(0x43, bytes([hi, lo]))


# ── Parser ─────────────────────────────────────

def parse_packet(data: bytes) -> dict:
    if len(data) < 4:
        return {"type": "unknown", "raw": data.hex()}
    length, header = struct.unpack_from("<HH", data, 0)
    payload = data[4:]

    if header == 0x10:
        serial = struct.unpack_from("<I", payload, 0)[0] if len(payload) >= 4 else 0
        return {"type": "serial_number", "serial": serial}

    if header == 0x40:
        return _parse_xbus(payload)

    if header == 0x84:
        return _parse_systemstate(payload)

    return {"type": "unknown", "header": hex(header), "payload": payload.hex()}


def _parse_xbus(payload: bytes) -> dict:
    if len(payload) < 1:
        return {"type": "xbus_short"}

    xheader = payload[0]

    # Gleisstatus
    if xheader == 0x61 and len(payload) >= 2:
        db0 = payload[1]
        if db0 == 0x00: return {"type": "track_power_off"}
        if db0 == 0x01: return {"type": "track_power_on"}
        if db0 == 0x02: return {"type": "programming_mode"}
        if db0 == 0x08: return {"type": "track_short_circuit"}

    if xheader == 0x81:
        return {"type": "emergency_stop"}

    # Lok-Info (xheader=0xEF, DB0=0x04 für 128er)
    if xheader == 0xEF and len(payload) >= 7:
        addr_hi = payload[2] & 0x3F
        addr_lo = payload[3]
        address = (addr_hi << 8) | addr_lo
        speed_byte = payload[5]
        forward = bool(speed_byte & 0x80)
        speed = speed_byte & 0x7F

        # F0–F4
        f_byte1 = payload[6]
        functions = {
            0: bool(f_byte1 & 0x10),
            1: bool(f_byte1 & 0x01),
            2: bool(f_byte1 & 0x02),
            3: bool(f_byte1 & 0x04),
            4: bool(f_byte1 & 0x08),
        }
        # F5–F12
        if len(payload) >= 9:
            f2 = payload[7]
            f3 = payload[8]
            for i in range(8): functions[5+i]  = bool(f2 & (1 << i))
            for i in range(4): functions[13+i] = bool(f3 & (1 << i))
            for i in range(4): functions[17+i] = bool(f3 & (1 << (i+4)))
        # F21–F28
        if len(payload) >= 10:
            f4 = payload[9]
            for i in range(8): functions[21+i] = bool(f4 & (1 << i))

        return {
            "type": "loco_info",
            "address": address,
            "speed": speed,
            "forward": forward,
            "functions": functions,
        }

    # Weichen-Info
    # Antwortformat: xheader=0x43, DB1=addrHi, DB2=addrLo, DB3=nibble
    # nibble bit1=output2(thrown), bit0=output1(straight)
    # bit3=aktiviert
    if xheader == 0x43 and len(payload) >= 4:
        addr_hi = payload[1]
        addr_lo = payload[2]
        address = ((addr_hi << 8) | addr_lo) + 1
        nibble = payload[3]
        # output1 aktiv (bit0=1) → gerade
        # output2 aktiv (bit1=1) → abbiegen
        # Wenn keine Spule aktiv: letzten Zustand beibehalten per bit2
        output1 = bool(nibble & 0x01)  # gerade
        output2 = bool(nibble & 0x02)  # abbiegen
        if output2:
            thrown = True
        elif output1:
            thrown = False
        else:
            # Kein Output aktiv → bit2 zeigt letzten Zustand
            thrown = bool(nibble & 0x04)
        return {"type": "turnout_info", "address": address, "thrown": thrown}

    return {"type": "xbus_unknown", "xheader": hex(xheader), "payload": payload.hex()}


def _parse_systemstate(payload: bytes) -> dict:
    if len(payload) < 16:
        return {"type": "systemstate_short"}
    main_current   = struct.unpack_from("<H", payload, 0)[0]
    temperature    = struct.unpack_from("<H", payload, 6)[0]
    supply_voltage = struct.unpack_from("<H", payload, 8)[0]
    central_state  = payload[12]
    return {
        "type": "systemstate",
        "main_current_ma":  main_current,
        "temperature_c":    temperature / 10,
        "supply_voltage_mv": supply_voltage,
        "track_power":      not bool(central_state & 0x02),
        "emergency_stop":   bool(central_state & 0x01),
        "short_circuit":    bool(central_state & 0x04),
        "programming_mode": bool(central_state & 0x20),
    }
