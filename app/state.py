"""
RailController – App State
Hält den aktuellen Laufzeit-Zustand der Anlage im Speicher
und verteilt Updates per WebSocket an alle verbundenen Browser.
"""

import asyncio
import json
import logging
from typing import Optional

logger = logging.getLogger("railcontroller.state")


class LocoState:
    def __init__(self, address: int):
        self.address = address
        self.speed: int = 0
        self.forward: bool = True
        self.functions: dict[int, bool] = {i: False for i in range(29)}

    def to_dict(self) -> dict:
        return {
            "address": self.address,
            "speed": self.speed,
            "forward": self.forward,
            "functions": self.functions,
        }


class AppState:
    def __init__(self):
        self.track_power: bool = False
        self.emergency_stop: bool = False
        self.short_circuit: bool = False
        self.temperature_c: float = 0.0
        self.supply_voltage_mv: int = 0
        self.z21_connected: bool = False

        self.locos: dict[int, LocoState] = {}
        self.turnouts: dict[int, bool] = {}  # address -> thrown

        self._ws_clients: set = set()
        self._lock = asyncio.Lock()

    # ──────────────────────────────────────────
    # WebSocket Clients
    # ──────────────────────────────────────────

    async def register_ws(self, ws):
        async with self._lock:
            self._ws_clients.add(ws)

    async def unregister_ws(self, ws):
        async with self._lock:
            self._ws_clients.discard(ws)

    async def broadcast(self, event: dict):
        """Event an alle verbundenen WebSocket-Clients senden."""
        message = json.dumps(event)
        dead = set()
        for ws in list(self._ws_clients):
            try:
                await ws.send_text(message)
            except Exception:
                dead.add(ws)
        async with self._lock:
            self._ws_clients -= dead

    # ──────────────────────────────────────────
    # Z21 Event-Handler
    # ──────────────────────────────────────────

    def handle_z21_event(self, event: dict):
        """Wird von Z21Client aufgerufen bei jedem eingehenden UDP-Paket."""
        asyncio.create_task(self._process_event(event))

    async def _process_event(self, event: dict):
        t = event.get("type")

        if t == "track_power_on":
            self.track_power = True
            self.emergency_stop = False
            await self.broadcast({"type": "system", "track_power": True, "emergency_stop": False})

        elif t == "track_power_off":
            self.track_power = False
            await self.broadcast({"type": "system", "track_power": False, "emergency_stop": False})

        elif t == "emergency_stop":
            self.emergency_stop = True
            for loco in self.locos.values():
                loco.speed = 0
            await self.broadcast({"type": "system", "track_power": self.track_power, "emergency_stop": True})

        elif t == "track_short_circuit":
            self.short_circuit = True
            self.track_power = False
            await self.broadcast({"type": "system", "track_power": False, "emergency_stop": False, "short_circuit": True})

        elif t == "systemstate":
            self.track_power = event.get("track_power", False)
            self.emergency_stop = event.get("emergency_stop", False)
            self.temperature_c = event.get("temperature_c", 0.0)
            self.supply_voltage_mv = event.get("supply_voltage_mv", 0)
            self.z21_connected = True
            await self.broadcast({
                "type": "system",
                "track_power": self.track_power,
                "emergency_stop": self.emergency_stop,
                "temperature_c": self.temperature_c,
                "supply_voltage_mv": self.supply_voltage_mv,
                "z21_connected": True,
            })

        elif t == "loco_info":
            addr = event["address"]
            if addr not in self.locos:
                self.locos[addr] = LocoState(addr)
            loco = self.locos[addr]
            loco.speed = event.get("speed", 0)
            loco.forward = event.get("forward", True)
            funcs = event.get("functions", {})
            for k, v in funcs.items():
                loco.functions[int(k)] = v
            await self.broadcast({"type": "loco_info", **loco.to_dict()})

        elif t == "turnout_info":
            addr = event["address"]
            self.turnouts[addr] = event.get("thrown", False)
            await self.broadcast({"type": "turnout_info", "address": addr, "thrown": event.get("thrown", False)})

    def get_loco_state(self, address: int) -> LocoState:
        if address not in self.locos:
            self.locos[address] = LocoState(address)
        return self.locos[address]

    def full_state(self) -> dict:
        return {
            "type": "full_state",
            "track_power": self.track_power,
            "emergency_stop": self.emergency_stop,
            "z21_connected": self.z21_connected,
            "temperature_c": self.temperature_c,
            "supply_voltage_mv": self.supply_voltage_mv,
            "locos": {str(k): v.to_dict() for k, v in self.locos.items()},
            "turnouts": {str(k): v for k, v in self.turnouts.items()},
        }


# Globale Singleton-Instanz
app_state = AppState()
