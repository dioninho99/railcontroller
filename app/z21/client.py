"""
RailController – Z21 UDP Client
"""

import asyncio
import logging
import time
from typing import Callable, Optional
from .protocol import (
    Z21_UDP_PORT, parse_packet,
    build_set_broadcast_flags, build_get_serial_number,
    build_systemstate_get, build_logoff,
    build_track_power_on, build_track_power_off, build_emergency_stop,
    build_loco_drive, build_loco_function, build_get_loco_info,
    build_set_turnout, build_get_turnout_info,
    BROADCAST_DRIVING_SWITCHING, BROADCAST_SYSTEMSTATE_CHANGES,
)

logger = logging.getLogger("railcontroller.z21")

# Timeout in Sekunden – keine Antwort → offline
OFFLINE_TIMEOUT = 15


class Z21Client:
    def __init__(self, host: str, port: int = Z21_UDP_PORT):
        self.host = host
        self.port = port
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._protocol = None
        self._callbacks: list[Callable[[dict], None]] = []
        self._connected = False
        self._last_seen: float = 0.0
        self._keepalive_task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None

    # ── Verbindung ─────────────────────────────

    async def connect(self):
        loop = asyncio.get_running_loop()
        self._protocol = _Z21Protocol(self._on_packet_received)
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: self._protocol,
            remote_addr=(self.host, self.port),
        )
        self._connected = True
        logger.info(f"Z21 UDP bereit: {self.host}:{self.port}")

        await self.send_raw(build_set_broadcast_flags(
            BROADCAST_DRIVING_SWITCHING | BROADCAST_SYSTEMSTATE_CHANGES
        ))
        await self.send_raw(build_get_serial_number())
        await self.send_raw(build_systemstate_get())

        self._keepalive_task = asyncio.create_task(self._keepalive())
        self._watchdog_task  = asyncio.create_task(self._watchdog())

    async def disconnect(self):
        for t in [self._keepalive_task, self._watchdog_task]:
            if t:
                t.cancel()
        if self._transport:
            try:
                await self.send_raw(build_logoff())
            except Exception:
                pass
            self._transport.close()
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── Senden ─────────────────────────────────

    async def send_raw(self, packet: bytes):
        if self._transport and not self._transport.is_closing():
            self._transport.sendto(packet)
            await asyncio.sleep(0)

    # ── Gleis ──────────────────────────────────

    async def track_power_on(self):
        await self.send_raw(build_track_power_on())

    async def track_power_off(self):
        await self.send_raw(build_track_power_off())

    async def emergency_stop(self):
        await self.send_raw(build_emergency_stop())

    # ── Lok ────────────────────────────────────

    async def get_loco_info(self, address: int):
        await self.send_raw(build_get_loco_info(address))

    async def loco_drive(self, address: int, speed: int, forward: bool = True):
        speed = max(0, min(127, speed))
        await self.send_raw(build_loco_drive(address, speed, forward))

    async def loco_stop(self, address: int):
        await self.send_raw(build_loco_drive(address, 0, True))

    async def loco_function(self, address: int, function: int, state: bool):
        await self.send_raw(build_loco_function(address, function, state))

    async def loco_function_toggle(self, address: int, function: int, current_state: bool):
        await self.loco_function(address, function, not current_state)

    # ── Weichen ────────────────────────────────

    async def get_turnout_info(self, address: int):
        await self.send_raw(build_get_turnout_info(address))

    async def set_turnout(self, address: int, thrown: bool):
        """
        Z21-Protokoll: activate=1 schaltet, activate=0 deaktiviert die Spule.
        Zwischen beiden Paketen mind. 200ms warten (Motorschutz).
        Danach Status abfragen.
        """
        await self.send_raw(build_set_turnout(address, thrown, activate=True))
        await asyncio.sleep(0.25)
        await self.send_raw(build_set_turnout(address, thrown, activate=False))
        await asyncio.sleep(0.1)
        # Zustand zurückfragen damit UI korrekt aktualisiert wird
        await self.send_raw(build_get_turnout_info(address))

    # ── Events ─────────────────────────────────

    def add_callback(self, callback: Callable[[dict], None]):
        self._callbacks.append(callback)

    def remove_callback(self, callback: Callable[[dict], None]):
        self._callbacks.discard(callback) if hasattr(self._callbacks, 'discard') else None

    def _on_packet_received(self, data: bytes):
        self._last_seen = time.monotonic()
        try:
            event = parse_packet(data)
            logger.debug(f"Z21 << {event}")
            for cb in self._callbacks:
                try:
                    cb(event)
                except Exception as e:
                    logger.error(f"Callback-Fehler: {e}")
        except Exception as e:
            logger.error(f"Parse-Fehler: {e} raw={data.hex()}")

    # ── Keepalive + Watchdog ────────────────────

    async def _keepalive(self):
        """Alle 10s Systemstatus anfordern damit Z21 uns nicht abmeldet."""
        while True:
            try:
                await asyncio.sleep(10)
                await self.send_raw(build_systemstate_get())
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Keepalive: {e}")

    async def _watchdog(self):
        """Prüft ob Z21 noch antwortet. Meldet offline wenn OFFLINE_TIMEOUT überschritten."""
        await asyncio.sleep(5)  # Startzeit abwarten
        was_online = False
        while True:
            try:
                await asyncio.sleep(5)
                age = time.monotonic() - self._last_seen
                online = self._last_seen > 0 and age < OFFLINE_TIMEOUT
                if online and not was_online:
                    was_online = True
                    logger.info("Z21 online")
                    for cb in self._callbacks:
                        try: cb({"type": "z21_online"})
                        except Exception: pass
                elif not online and was_online:
                    was_online = False
                    logger.warning(f"Z21 offline (keine Antwort seit {age:.0f}s)")
                    for cb in self._callbacks:
                        try: cb({"type": "z21_offline"})
                        except Exception: pass
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Watchdog: {e}")


class _Z21Protocol(asyncio.DatagramProtocol):
    def __init__(self, on_received: Callable[[bytes], None]):
        self._on_received = on_received

    def datagram_received(self, data: bytes, addr):
        self._on_received(data)

    def error_received(self, exc):
        logger.error(f"UDP-Fehler: {exc}")

    def connection_lost(self, exc):
        if exc:
            logger.warning(f"UDP connection_lost: {exc}")
