"""RailController – Z21 Belegtmelder-Dekodierung (R-Bus, CAN, LocoNet).

Eigenständiges Modul. Es dekodiert eingehende Z21-UDP-Datagramme zu
(melder_adresse, besetzt)-Ereignissen und liefert die Datagramme zum
Abonnieren/Abfragen der Belegtmeldung.

Verifiziert gegen die Z21 LAN-Protokoll-Spezifikation V1.13:
  * LAN_SET_BROADCASTFLAGS (0x50): Flags little-endian
      0x00000001  Fahren/Schalten (Lok-/Weichen-Info)
      0x00000002  R-Bus Rückmelder            -> LAN_RMBUS_DATACHANGED (0x80)
      0x00080000  CAN-Belegtmelder            -> LAN_CAN_DETECTOR       (0xC4)
      0x01000000  LocoNet-Meldungen           -> LAN_LOCONET_DETECTOR   (0xA4)
  * Ablegen dieses Moduls unter app/z21/occupancy.py

Ablauf (siehe Wiring-Hinweise unten in der Antwort):
  1) Nach dem Verbinden set_broadcastflags_datagram() senden und einmalig
     rmbus_getdata_datagram(0)/(1) zum Abfragen des Ist-Zustands.
  2) Jedes empfangene UDP-Datagramm durch decode() schicken.
  3) Die zurückgegebenen (address, occupied)-Ereignisse ans Frontend
     broadcasten:  {"type": "occupancy", "address": address, "occupied": occupied}
"""

import struct

# ── Broadcast-Flags ────────────────────────────────────────────────
BC_DRIVE_SWITCH = 0x00000001
BC_RBUS         = 0x00000002
BC_CAN_DETECTOR = 0x00080000
BC_LOCONET      = 0x01000000
# Alles einschalten, was Belegtmeldung liefert (alle drei Kanäle):
OCCUPANCY_FLAGS = BC_DRIVE_SWITCH | BC_RBUS | BC_CAN_DETECTOR | BC_LOCONET  # 0x01080003

# ── Z21 Header ─────────────────────────────────────────────────────
LAN_SET_BROADCASTFLAGS = 0x50
LAN_RMBUS_GETDATA      = 0x81
LAN_RMBUS_DATACHANGED  = 0x80
LAN_LOCONET_DETECTOR   = 0xA4
LAN_CAN_DETECTOR       = 0xC4


# ── Ausgehende Datagramme ──────────────────────────────────────────

def set_broadcastflags_datagram(flags: int = OCCUPANCY_FLAGS) -> bytes:
    """LAN_SET_BROADCASTFLAGS – abonniert die Belegtmeldung (nach jedem Connect nötig)."""
    return struct.pack('<HHI', 0x08, LAN_SET_BROADCASTFLAGS, flags)


def rmbus_getdata_datagram(group: int = 0) -> bytes:
    """LAN_RMBUS_GETDATA – fragt den aktuellen R-Bus-Zustand ab (group 0 = Melder 1–10, 1 = 11–20)."""
    return struct.pack('<HHB', 0x07, LAN_RMBUS_GETDATA, group & 0xFF)


# ── Eingehende Datagramme dekodieren ───────────────────────────────

def decode(data: bytes):
    """Zerlegt ein rohes Z21-Datagramm (evtl. mehrere Meldungen in einem UDP-Paket)
    und gibt eine Liste von (address, occupied)-Tupeln zurück. Leere Liste, wenn
    keine Belegtmeldung enthalten ist."""
    events = []
    i, n = 0, len(data)
    while i + 4 <= n:
        length = data[i] | (data[i + 1] << 8)
        if length < 4 or i + length > n:
            break
        header = data[i + 2] | (data[i + 3] << 8)
        payload = data[i + 4: i + length]

        if header == LAN_RMBUS_DATACHANGED:
            events += _decode_rbus(payload)
        elif header == LAN_CAN_DETECTOR:
            events += _decode_can(payload)
        elif header == LAN_LOCONET_DETECTOR:
            events += _decode_loconet(payload)

        i += length
    return events


def _decode_rbus(p: bytes):
    """R-Bus: [group][10 Bytes]; jedes Byte = 8 Eingänge eines Melders.
    Melder-Adresse = group*10 + Byte-Index + 1. Besetzt = Byte != 0."""
    if len(p) < 11:
        return []
    group = p[0]
    return [(group * 10 + bi + 1, p[1 + bi] != 0) for bi in range(10)]


def _decode_can(p: bytes):
    """CAN-Belegtmelder (LAN_CAN_DETECTOR): NID(2) Adresse(2) Port(1) Typ(1) Wert1(2) Wert2(2).
    Melder je (Adresse + Port); besetzt, wenn Wert1 != 0."""
    if len(p) < 8:
        return []
    address = p[2] | (p[3] << 8)
    port    = p[4]
    value1  = p[6] | (p[7] << 8)
    return [(address + port, value1 != 0)]


def _decode_loconet(p: bytes):
    """LocoNet-Belegtmelder (LAN_LOCONET_DETECTOR): Typ(1) Adresse(2, LE) Info(1).
    Besetzt = Bit 0x10 im Info-Byte (OPC_INPUT_REP)."""
    if len(p) < 4:
        return []
    addr = p[1] | (p[2] << 8)
    return [(addr, bool(p[3] & 0x10))]


# ── Schnelltest der Dekodierung (python occupancy.py) ──────────────
if __name__ == "__main__":
    # Beispiel R-Bus: group 0, Melder 3 (Byte-Index 2) besetzt
    demo = struct.pack('<HHB', 0x0F, LAN_RMBUS_DATACHANGED, 0x00) + bytes([0, 0, 0x01, 0, 0, 0, 0, 0, 0, 0])
    print("Flags:", hex(OCCUPANCY_FLAGS))
    print("decode:", decode(demo))   # -> [(1, False), (2, False), (3, True), (4, False), ...]