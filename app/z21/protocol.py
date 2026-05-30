"""
RailController – Z21 LAN Protocol
Basiert auf: Roco Z21 LAN Protocol Specification V1.13
UDP Port: 21105
"""

import struct

# ──────────────────────────────────────────────
# Header-Konstanten
# ──────────────────────────────────────────────
Z21_UDP_PORT = 21105

# DataLen (2 Byte) + Header (2 Byte) + Daten
# Alle Werte Little-Endian

# ──────────────────────────────────────────────
# X-BUS Header (in Datenbytes eingebettet)
# ──────────────────────────────────────────────
HEADER_LAN_GET_SERIAL_NUMBER     = 0x10
HEADER_LAN_GET_HW_INFO           = 0x1A
HEADER_LAN_LOGOFF                = 0x30
HEADER_LAN_X_HEADER             = 0x40  # X-Bus Befehle
HEADER_LAN_SET_BROADCASTFLAGS    = 0x50
HEADER_LAN_GET_BROADCASTFLAGS    = 0x51
HEADER_LAN_GET_LOCOMODE          = 0x60
HEADER_LAN_SET_LOCOMODE          = 0x61
HEADER_LAN_RMBUS_GETDATA         = 0x81
HEADER_LAN_RMBUS_PROGRAMMODULE  = 0x82
HEADER_LAN_SYSTEMSTATE_GETDATA   = 0x85
HEADER_LAN_LOCONET_DISPATCH_ADDR = 0xA3

# ──────────────────────────────────────────────
# X-Bus Befehle (X-Header Byte)
# ──────────────────────────────────────────────
XHEADER_GET_VERSION              = 0x21
XHEADER_GET_STATUS               = 0x21
XHEADER_SET_TRACK_POWER_OFF      = 0x21
XHEADER_SET_TRACK_POWER_ON       = 0x21
XHEADER_BC_TRACK_POWER_OFF       = 0x61
XHEADER_BC_TRACK_POWER_ON        = 0x61
XHEADER_BC_PROGRAMMING_MODE      = 0x61
XHEADER_BC_TRACK_SHORT_CIRCUIT   = 0x61
XHEADER_LOCO_INFO                = 0xEF
XHEADER_LOCO_DRIVE               = 0xE4
XHEADER_LOCO_FUNCTION            = 0xF8
XHEADER_GET_LOCO_INFO            = 0xE3
XHEADER_SET_TURNOUT              = 0x53
XHEADER_GET_TURNOUT_INFO         = 0x43
XHEADER_BC_TURNOUT_INFO          = 0x43
XHEADER_SET_STOP                 = 0x80
XHEADER_CV_READ                  = 0x23
XHEADER_CV_WRITE                 = 0x24
XHEADER_CV_POM_WRITE_BYTE        = 0xE6

# ──────────────────────────────────────────────
# Broadcast-Flags
# ──────────────────────────────────────────────
BROADCAST_DRIVING_SWITCHING      = 0x00000001
BROADCAST_RBUS_CHANGES           = 0x00000002
BROADCAST_RAILCOM_CHANGES        = 0x00000004
BROADCAST_SYSTEMSTATE_CHANGES    = 0x00000100
BROADCAST_DRIVING_SWITCHING_EX   = 0x00010000

# ──────────────────────────────────────────────
# Fahrstufen-Modus
# ──────────────────────────────────────────────
SPEED_STEPS_14  = 0
SPEED_STEPS_28  = 2
SPEED_STEPS_128 = 4

# ──────────────────────────────────────────────
# Hilfsfunktionen zum Bauen von UDP-Paketen
# ──────────────────────────────────────────────

def _build_packet(header: int, data: bytes = b"") -> bytes:
    """Erstellt ein vollständiges Z21 UDP-Paket."""
    length = 4 + len(data)
    return struct.pack("<HH", length, header) + data


def _xor_byte(data: bytes) -> int:
    """Berechnet XOR-Checksum über alle Bytes."""
    result = 0
    for b in data:
        result ^= b
    return result


def _build_xbus_packet(xheader: int, db: bytes) -> bytes:
    """Erstellt ein X-Bus Paket (eingebettet in LAN_X)."""
    xor = _xor_byte(bytes([xheader]) + db)
    data = bytes([xheader]) + db + bytes([xor])
    return _build_packet(HEADER_LAN_X_HEADER, data)


# ──────────────────────────────────────────────
# Paket-Builder
# ──────────────────────────────────────────────

def build_get_serial_number() -> bytes:
    return _build_packet(HEADER_LAN_GET_SERIAL_NUMBER)


def build_get_hw_info() -> bytes:
    return _build_packet(HEADER_LAN_GET_HW_INFO)


def build_logoff() -> bytes:
    return _build_packet(HEADER_LAN_LOGOFF)


def build_get_status() -> bytes:
    return _build_xbus_packet(XHEADER_GET_STATUS, bytes([0x24]))


def build_track_power_on() -> bytes:
    return _build_xbus_packet(XHEADER_SET_TRACK_POWER_ON, bytes([0x81]))


def build_track_power_off() -> bytes:
    return _build_xbus_packet(XHEADER_SET_TRACK_POWER_OFF, bytes([0x80]))


def build_emergency_stop() -> bytes:
    return _build_xbus_packet(XHEADER_SET_STOP, b"")


def build_set_broadcast_flags(flags: int = BROADCAST_DRIVING_SWITCHING | BROADCAST_SYSTEMSTATE_CHANGES) -> bytes:
    data = struct.pack("<I", flags)
    return _build_packet(HEADER_LAN_SET_BROADCASTFLAGS, data)


def build_get_loco_info(address: int) -> bytes:
    """Lok-Info anfordern."""
    hi = (address >> 8) | 0xC0
    lo = address & 0xFF
    return _build_xbus_packet(XHEADER_GET_LOCO_INFO, bytes([0x05, hi, lo]))


def build_loco_drive(address: int, speed: int, forward: bool, speed_steps: int = SPEED_STEPS_128) -> bytes:
    """
    Lokfahrbefehl.
    speed: 0 = Stop, 1 = Notstop, 2–127 = Fahrstufen (bei 128 Stufen)
    """
    hi = (address >> 8) | 0xC0
    lo = address & 0xFF

    if speed_steps == SPEED_STEPS_128:
        db0 = 0x13
    elif speed_steps == SPEED_STEPS_28:
        db0 = 0x12
    else:
        db0 = 0x10  # 14 Stufen

    direction_bit = 0x80 if forward else 0x00
    speed_byte = (speed & 0x7F) | direction_bit

    return _build_xbus_packet(XHEADER_LOCO_DRIVE, bytes([db0, hi, lo, speed_byte]))


def build_loco_function(address: int, function: int, state: bool) -> bytes:
    """
    Lokfunktion schalten.
    function: 0 (Licht) bis 28
    state: True = ein, False = aus
    """
    hi = (address >> 8) | 0xC0
    lo = address & 0xFF

    if function <= 12:
        group = 0
    elif function <= 20:
        group = 1
    elif function <= 28:
        group = 2
    else:
        group = 3

    # Funktionsgruppen-Byte aufbauen
    if function == 0:
        func_byte = 0x10 if state else 0x00
    elif function <= 4:
        shift = function - 1
        func_byte = (1 << shift) if state else 0x00
    else:
        shift = (function - 5) % 8
        func_byte = (1 << shift) if state else 0x00

    return _build_xbus_packet(XHEADER_LOCO_FUNCTION, bytes([group, hi, lo, func_byte]))


def build_set_turnout(address: int, thrown: bool, activate: bool = True) -> bytes:
    """
    Weichenbefehl.
    address: Weichenadresse (1-basiert)
    thrown: True = abbiegen, False = gerade
    activate: True = Spule aktivieren, False = deaktivieren
    """
    addr = address - 1  # 0-basiert intern
    hi = (addr >> 8) & 0xFF
    lo = addr & 0xFF
    nibble = (0x08 if activate else 0x00) | (0x01 if not thrown else 0x00)
    return _build_xbus_packet(XHEADER_SET_TURNOUT, bytes([hi, lo, nibble]))


def build_get_turnout_info(address: int) -> bytes:
    addr = address - 1
    hi = (addr >> 8) & 0xFF
    lo = addr & 0xFF
    return _build_xbus_packet(XHEADER_GET_TURNOUT_INFO, bytes([hi, lo]))


def build_systemstate_get() -> bytes:
    return _build_packet(HEADER_LAN_SYSTEMSTATE_GETDATA)


# ──────────────────────────────────────────────
# Paket-Parser
# ──────────────────────────────────────────────

def parse_packet(data: bytes) -> dict:
    """Eingehendes UDP-Paket parsen und als Dict zurückgeben."""
    if len(data) < 4:
        return {"type": "unknown", "raw": data.hex()}

    length, header = struct.unpack_from("<HH", data, 0)
    payload = data[4:]

    if header == 0x10:
        serial = struct.unpack_from("<I", payload, 0)[0]
        return {"type": "serial_number", "serial": serial}

    if header == 0x40:  # X-Bus Paket
        return _parse_xbus(payload)

    if header == 0x84:  # SystemState
        return _parse_systemstate(payload)

    if header == 0x85:
        return {"type": "systemstate_request"}

    return {"type": "unknown", "header": hex(header), "payload": payload.hex()}


def _parse_xbus(payload: bytes) -> dict:
    if len(payload) < 2:
        return {"type": "xbus_short"}

    xheader = payload[0]

    # Gleis-Status
    if xheader == 0x61:
        db0 = payload[1] if len(payload) > 1 else 0
        if db0 == 0x00:
            return {"type": "track_power_off"}
        if db0 == 0x01:
            return {"type": "track_power_on"}
        if db0 == 0x02:
            return {"type": "programming_mode"}
        if db0 == 0x08:
            return {"type": "track_short_circuit"}

    # Nothalt
    if xheader == 0x81:
        return {"type": "emergency_stop"}

    # Lok-Info
    if xheader == 0xEF and len(payload) >= 8:
        addr_hi = payload[2] & 0x3F
        addr_lo = payload[3]
        address = (addr_hi << 8) | addr_lo
        speed_byte = payload[5]
        forward = bool(speed_byte & 0x80)
        speed = speed_byte & 0x7F
        f0 = bool(payload[6] & 0x10)
        f1 = bool(payload[6] & 0x01)
        f2 = bool(payload[6] & 0x02)
        f3 = bool(payload[6] & 0x04)
        f4 = bool(payload[6] & 0x08)
        functions = {0: f0, 1: f1, 2: f2, 3: f3, 4: f4}
        if len(payload) >= 9:
            for i in range(8):
                functions[5 + i] = bool(payload[7] & (1 << i))
        if len(payload) >= 10:
            for i in range(8):
                functions[13 + i] = bool(payload[8] & (1 << i))
        if len(payload) >= 11:
            for i in range(8):
                functions[21 + i] = bool(payload[9] & (1 << i))
        return {
            "type": "loco_info",
            "address": address,
            "speed": speed,
            "forward": forward,
            "functions": functions,
        }

    # Weichen-Info
    if xheader == 0x43 and len(payload) >= 4:
        addr_hi = payload[1]
        addr_lo = payload[2]
        address = ((addr_hi << 8) | addr_lo) + 1
        nibble = payload[3]
        thrown = not bool(nibble & 0x01)
        return {"type": "turnout_info", "address": address, "thrown": thrown}

    return {"type": "xbus_unknown", "xheader": hex(xheader), "payload": payload.hex()}


def _parse_systemstate(payload: bytes) -> dict:
    if len(payload) < 16:
        return {"type": "systemstate_short"}
    main_current    = struct.unpack_from("<H", payload, 0)[0]
    prog_current    = struct.unpack_from("<H", payload, 2)[0]
    filtered_main   = struct.unpack_from("<H", payload, 4)[0]
    temperature     = struct.unpack_from("<H", payload, 6)[0]
    supply_voltage  = struct.unpack_from("<H", payload, 8)[0]
    vcc_voltage     = struct.unpack_from("<H", payload, 10)[0]
    central_state   = payload[12]
    return {
        "type": "systemstate",
        "main_current_ma": main_current,
        "prog_current_ma": prog_current,
        "temperature_c": temperature / 10,
        "supply_voltage_mv": supply_voltage,
        "vcc_voltage_mv": vcc_voltage,
        "track_power": not bool(central_state & 0x02),
        "emergency_stop": bool(central_state & 0x01),
        "short_circuit": bool(central_state & 0x04),
        "programming_mode": bool(central_state & 0x20),
    }
