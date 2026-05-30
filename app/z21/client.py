"""
RailController – Z21 UDP Client
Verwaltet die asynchrone UDP-Verbindung zur Z21 Start.
"""

import asyncio
import logging
from typing import Callable, Optional
from .protocol import (
    Z21_UDP_PORT,
    parse_packet,
    build_set_broadcast_flags,
    build_get_serial_number,
    build_systemstate_get,
    build_logoff,
    build_track_power_on,
    build_track_power_off,
    build_emergency_stop,
    build_loco_drive,
    build_loco_function,
    build_get_loco_info,
    build_set_turnout,
    build_get_turnout_info,
    BROADCAST_DRIVING_SWITCHING,
    BROADCAST_SYSTEMSTATE_CHANGES,
)

logger = logging.getLogger("railcontroller.z21")


class Z21Client:
    """
    Asynchroner UDP-Client für die Z21 Start.
    Hält die Verbindung aufrecht und leitet eingehende Events
    an registrierte Callback-Funktionen weiter.
    """

    def __init__(self, host: str, port: int = Z21_UDP_PORT):
        self.host = host
        self.port = port
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._protocol: Optional["_Z21Protocol"] = None
        self._callbacks: list[Callable[[dict], None]] = []
        self._connected = False
        self._keepalive_task: Optional[asyncio.Task] = None

    # ──────────────────────────────────────────
    # Verbindung
    # ──────────────────────────────────────────

    async def connect(self):
        """Verbindung zur Z21 herstellen."""
        loop = asyncio.get_running_loop()
        self._protocol = _Z21Protocol(self._on_packet_received)
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: self._protocol,
            remote_addr=(self.host, self.port),
        )
        self._connected = True
        logger.info(f"Verbunden mit Z21 auf {self.host}:{self.port}")

        # Broadcast-Flags setzen und Status abrufen
        await self.send_raw(build_set_broadcast_flags(
            BROADCAST_DRIVING_SWITCHING | BROADCAST_SYSTEMSTATE_CHANGES
        ))
        await self.send_raw(build_get_serial_number())
        await self.send_raw(build_systemstate_get())

        # Keepalive starten (alle 30 Sekunden Status abfragen)
        self._keepalive_task = asyncio.create_task(self._keepalive())

    async def disconnect(self):
        """Verbindung sauber trennen."""
        if self._keepalive_task:
            self._keepalive_task.cancel()
        if self._transport:
            await self.send_raw(build_logoff())
            self._transport.close()
        self._connected = False
        logger.info("Z21 Verbindung getrennt")

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ──────────────────────────────────────────
    # Senden
    # ──────────────────────────────────────────

    async def send_raw(self, packet: bytes):
        """Rohe Bytes an die Z21 senden."""
        if self._transport and not self._transport.is_closing():
            self._transport.sendto(packet)
            await asyncio.sleep(0)  # Eventloop yielden

    # ──────────────────────────────────────────
    # Gleis-Steuerung
    # ──────────────────────────────────────────

    async def track_power_on(self):
        await self.send_raw(build_track_power_on())

    async def track_power_off(self):
        await self.send_raw(build_track_power_off())

    async def emergency_stop(self):
        await self.send_raw(build_emergency_stop())

    # ──────────────────────────────────────────
    # Lok-Steuerung
    # ──────────────────────────────────────────

    async def get_loco_info(self, address: int):
        await self.send_raw(build_get_loco_info(address))

    async def loco_drive(self, address: int, speed: int, forward: bool = True):
        """
        Lok fahren.
        address: DCC-Adresse
        speed:   0 = Stop, 1–126 = Fahrstufen, 127 = max
        forward: Fahrtrichtung
        """
        speed = max(0, min(127, speed))
        await self.send_raw(build_loco_drive(address, speed, forward))

    async def loco_stop(self, address: int):
        """Lok sanft stoppen (Geschwindigkeit 0)."""
        await self.send_raw(build_loco_drive(address, 0, True))

    async def loco_function(self, address: int, function: int, state: bool):
        """Lokfunktion ein- oder ausschalten (F0–F28)."""
        await self.send_raw(build_loco_function(address, function, state))

    async def loco_function_toggle(self, address: int, function: int, current_state: bool):
        """Lokfunktion umschalten."""
        await self.loco_function(address, function, not current_state)

    # ──────────────────────────────────────────
    # Weichen-Steuerung
    # ──────────────────────────────────────────

    async def get_turnout_info(self, address: int):
        await self.send_raw(build_get_turnout_info(address))

    async def set_turnout(self, address: int, thrown: bool):
        """
        Weiche stellen.
        thrown: True = abbiegen, False = gerade
        """
        # Zuerst aktivieren, dann nach kurzer Pause deaktivieren
        await self.send_raw(build_set_turnout(address, thrown, activate=True))
        await asyncio.sleep(0.1)
        await self.send_raw(build_set_turnout(address, thrown, activate=False))

    # ──────────────────────────────────────────
    # Events / Callbacks
    # ──────────────────────────────────────────

    def add_callback(self, callback: Callable[[dict], None]):
        """Callback registrieren – wird bei jedem eingehenden Paket aufgerufen."""
        self._callbacks.append(callback)

    def remove_callback(self, callback: Callable[[dict], None]):
        self._callbacks.remove(callback)

    def _on_packet_received(self, data: bytes):
        """Interner Handler für eingehende UDP-Pakete."""
        try:
            event = parse_packet(data)
            logger.debug(f"Z21 Event: {event}")
            for cb in self._callbacks:
                try:
                    cb(event)
                except Exception as e:
                    logger.error(f"Callback-Fehler: {e}")
        except Exception as e:
            logger.error(f"Paket-Parse-Fehler: {e} – raw: {data.hex()}")

    # ──────────────────────────────────────────
    # Keepalive
    # ──────────────────────────────────────────

    async def _keepalive(self):
        """Alle 30 Sekunden Systemstatus abrufen, damit Z21 uns nicht abmeldet."""
        while True:
            try:
                await asyncio.sleep(30)
                await self.send_raw(build_systemstate_get())
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Keepalive-Fehler: {e}")


# ──────────────────────────────────────────────
# Asyncio Datagram Protocol
# ──────────────────────────────────────────────

class _Z21Protocol(asyncio.DatagramProtocol):
    def __init__(self, on_received: Callable[[bytes], None]):
        self._on_received = on_received

    def datagram_received(self, data: bytes, addr):
        self._on_received(data)

    def error_received(self, exc):
        logger.error(f"UDP-Fehler: {exc}")

    def connection_lost(self, exc):
        if exc:
            logger.warning(f"Verbindung verloren: {exc}")
