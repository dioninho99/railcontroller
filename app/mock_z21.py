"""
RailController – Mock Z21
Simuliert Z21-Events für lokales Entwickeln ohne echte Hardware.
Aktiviert via Umgebungsvariable Z21_MOCK=true
"""

import asyncio
import logging
import random

logger = logging.getLogger("railcontroller.mock")


class MockZ21Client:
    """Simuliert die Z21 mit zufälligen Events."""

    def __init__(self):
        self._callbacks = []
        self._connected = False
        self._task: asyncio.Task = None
        self.track_power = False
        # Simulierter Lok-Zustand
        self._locos = {
            3:  {"speed": 0, "forward": True, "functions": {i: False for i in range(29)}},
            5:  {"speed": 0, "forward": True, "functions": {i: False for i in range(29)}},
            10: {"speed": 0, "forward": True, "functions": {i: False for i in range(29)}},
        }
        self._turnouts = {1: False, 2: False, 3: True}

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self):
        self._connected = True
        logger.info("Mock Z21 gestartet (kein echtes Gerät)")
        # Initiale Events senden
        await asyncio.sleep(0.5)
        self._fire({"type": "serial_number", "serial": 99999999})
        self._fire({
            "type": "systemstate",
            "track_power": False,
            "emergency_stop": False,
            "temperature_c": 42.0,
            "supply_voltage_mv": 18000,
            "vcc_voltage_mv": 5000,
        })
        for addr, state in self._turnouts.items():
            self._fire({"type": "turnout_info", "address": addr, "thrown": state})
        self._task = asyncio.create_task(self._simulate())

    async def disconnect(self):
        if self._task:
            self._task.cancel()
        self._connected = False

    def add_callback(self, cb):
        self._callbacks.append(cb)

    def remove_callback(self, cb):
        self._callbacks.remove(cb)

    def _fire(self, event: dict):
        for cb in self._callbacks:
            try:
                cb(event)
            except Exception as e:
                logger.error(f"Mock callback error: {e}")

    async def _simulate(self):
        """Periodisch simulierte Loko-Infos senden."""
        while True:
            await asyncio.sleep(3)
            for addr, state in self._locos.items():
                self._fire({
                    "type": "loco_info",
                    "address": addr,
                    **state,
                })

    # ── Gleis ──────────────────────────────────

    async def track_power_on(self):
        self.track_power = True
        self._fire({"type": "track_power_on"})
        self._fire({
            "type": "systemstate",
            "track_power": True,
            "emergency_stop": False,
            "temperature_c": 42.0,
            "supply_voltage_mv": 18000,
        })

    async def track_power_off(self):
        self.track_power = False
        self._fire({"type": "track_power_off"})

    async def emergency_stop(self):
        for state in self._locos.values():
            state["speed"] = 0
        self._fire({"type": "emergency_stop"})

    # ── Lok ────────────────────────────────────

    async def get_loco_info(self, address: int):
        if address in self._locos:
            self._fire({"type": "loco_info", "address": address, **self._locos[address]})
        else:
            self._locos[address] = {"speed": 0, "forward": True, "functions": {i: False for i in range(29)}}
            self._fire({"type": "loco_info", "address": address, **self._locos[address]})

    async def loco_drive(self, address: int, speed: int, forward: bool = True):
        if address not in self._locos:
            self._locos[address] = {"speed": 0, "forward": True, "functions": {i: False for i in range(29)}}
        self._locos[address]["speed"] = speed
        self._locos[address]["forward"] = forward
        self._fire({"type": "loco_info", "address": address, **self._locos[address]})

    async def loco_stop(self, address: int):
        await self.loco_drive(address, 0, True)

    async def loco_function(self, address: int, function: int, state: bool):
        if address not in self._locos:
            self._locos[address] = {"speed": 0, "forward": True, "functions": {i: False for i in range(29)}}
        self._locos[address]["functions"][function] = state
        self._fire({"type": "loco_info", "address": address, **self._locos[address]})

    async def loco_function_toggle(self, address: int, function: int, current_state: bool):
        await self.loco_function(address, function, not current_state)

    # ── Weichen ────────────────────────────────

    async def get_turnout_info(self, address: int):
        thrown = self._turnouts.get(address, False)
        self._fire({"type": "turnout_info", "address": address, "thrown": thrown})

    async def set_turnout(self, address: int, thrown: bool):
        self._turnouts[address] = thrown
        self._fire({"type": "turnout_info", "address": address, "thrown": thrown})
