"""
RailController – App State
Laufzeit-Zustand der Anlage. Überlebt Tab-Wechsel da er serverseitig liegt.
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
            "address":   self.address,
            "speed":     self.speed,
            "forward":   self.forward,
            "functions": self.functions,
        }

    def update(self, event: dict):
        self.speed   = event.get("speed", self.speed)
        self.forward = event.get("forward", self.forward)
        funcs = event.get("functions", {})
        for k, v in funcs.items():
            self.functions[int(k)] = v


class AppState:
    def __init__(self):
        self.track_power:    bool  = False
        self.emergency_stop: bool  = False
        self.short_circuit:  bool  = False
        self.temperature_c:  float = 0.0
        self.supply_voltage_mv: int = 0
        self.z21_connected:  bool  = False

        self.locos:    dict[int, LocoState] = {}
        self.turnouts: dict[int, bool]      = {}

        self._ws_clients: set = set()
        self._lock = asyncio.Lock()

    # ── WebSocket ───────────────────────────────

    async def register_ws(self, ws):
        async with self._lock:
            self._ws_clients.add(ws)

    async def unregister_ws(self, ws):
        async with self._lock:
            self._ws_clients.discard(ws)

    async def broadcast(self, event: dict):
        message = json.dumps(event)
        dead = set()
        for ws in list(self._ws_clients):
            try:
                await ws.send_text(message)
            except Exception:
                dead.add(ws)
        if dead:
            async with self._lock:
                self._ws_clients -= dead

    # ── Z21 Events ──────────────────────────────

    def handle_z21_event(self, event: dict):
        asyncio.create_task(self._process(event))

    async def _process(self, event: dict):
        t = event.get("type")

        if t == "z21_online":
            self.z21_connected = True
            await self.broadcast({"type": "system", **self._sys_dict()})

        elif t == "z21_offline":
            self.z21_connected = False
            self.track_power   = False
            await self.broadcast({"type": "system", **self._sys_dict()})

        elif t == "track_power_on":
            self.track_power   = True
            self.emergency_stop = False
            await self.broadcast({"type": "system", **self._sys_dict()})

        elif t == "track_power_off":
            self.track_power = False
            await self.broadcast({"type": "system", **self._sys_dict()})

        elif t == "emergency_stop":
            self.emergency_stop = True
            for loco in self.locos.values():
                loco.speed = 0
            await self.broadcast({"type": "system", **self._sys_dict()})
            # Alle Lok-States broadcasten
            for loco in self.locos.values():
                await self.broadcast({"type": "loco_info", **loco.to_dict()})

        elif t == "track_short_circuit":
            self.short_circuit = True
            self.track_power   = False
            await self.broadcast({"type": "system", **self._sys_dict()})

        elif t == "systemstate":
            self.track_power       = event.get("track_power", False)
            self.emergency_stop    = event.get("emergency_stop", False)
            self.temperature_c     = event.get("temperature_c", 0.0)
            self.supply_voltage_mv = event.get("supply_voltage_mv", 0)
            self.z21_connected     = True
            await self.broadcast({"type": "system", **self._sys_dict()})

        elif t == "loco_info":
            addr = event["address"]
            if addr not in self.locos:
                self.locos[addr] = LocoState(addr)
            self.locos[addr].update(event)
            await self.broadcast({"type": "loco_info", **self.locos[addr].to_dict()})

        elif t == "turnout_info":
            addr = event["address"]
            self.turnouts[addr] = event.get("thrown", False)
            await self.broadcast({
                "type": "turnout_info",
                "address": addr,
                "thrown": self.turnouts[addr],
            })

    # ── Helpers ─────────────────────────────────

    def _sys_dict(self) -> dict:
        return {
            "track_power":       self.track_power,
            "emergency_stop":    self.emergency_stop,
            "short_circuit":     self.short_circuit,
            "z21_connected":     self.z21_connected,
            "temperature_c":     self.temperature_c,
            "supply_voltage_mv": self.supply_voltage_mv,
        }

    def get_loco_state(self, address: int) -> LocoState:
        if address not in self.locos:
            self.locos[address] = LocoState(address)
        return self.locos[address]

    def full_state(self) -> dict:
        return {
            "type": "full_state",
            **self._sys_dict(),
            "locos":    {str(k): v.to_dict() for k, v in self.locos.items()},
            "turnouts": {str(k): v for k, v in self.turnouts.items()},
        }


app_state = AppState()
