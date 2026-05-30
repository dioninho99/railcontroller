"""RailController – Lok API Router"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select
from pydantic import BaseModel
from typing import Optional
import json

from models.models import Locomotive
from state import app_state

router = APIRouter(prefix="/api/locos", tags=["locos"])


def get_session(request: Request):
    with Session(request.app.state.engine) as session:
        yield session


# ── Schemas ────────────────────────────────────

class LocoCreate(BaseModel):
    address: int
    name: str
    icon: str = "🚂"
    max_speed: int = 127
    speed_steps: int = 128
    function_names: dict = {}

class DriveCommand(BaseModel):
    speed: int
    forward: bool = True

class FunctionCommand(BaseModel):
    function: int
    state: bool


# ── Endpunkte ──────────────────────────────────

@router.get("/")
def list_locos(session: Session = Depends(get_session)):
    locos = session.exec(select(Locomotive)).all()
    result = []
    for loco in locos:
        state = app_state.get_loco_state(loco.address)
        result.append({
            "id": loco.id,
            "address": loco.address,
            "name": loco.name,
            "icon": loco.icon,
            "max_speed": loco.max_speed,
            "speed_steps": loco.speed_steps,
            "function_names": loco.get_function_names(),
            "state": state.to_dict(),
        })
    return result


@router.post("/")
def create_loco(data: LocoCreate, session: Session = Depends(get_session)):
    existing = session.exec(select(Locomotive).where(Locomotive.address == data.address)).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Adresse {data.address} bereits vergeben")
    loco = Locomotive(
        address=data.address,
        name=data.name,
        icon=data.icon,
        max_speed=data.max_speed,
        speed_steps=data.speed_steps,
        function_names=json.dumps(data.function_names),
    )
    session.add(loco)
    session.commit()
    session.refresh(loco)
    return loco


@router.delete("/{address}")
def delete_loco(address: int, session: Session = Depends(get_session)):
    loco = session.exec(select(Locomotive).where(Locomotive.address == address)).first()
    if not loco:
        raise HTTPException(status_code=404, detail="Lok nicht gefunden")
    session.delete(loco)
    session.commit()
    return {"ok": True}


@router.post("/{address}/drive")
async def drive(address: int, cmd: DriveCommand, request: Request):
    z21 = request.app.state.z21
    await z21.loco_drive(address, cmd.speed, cmd.forward)
    return {"ok": True}


@router.post("/{address}/stop")
async def stop(address: int, request: Request):
    z21 = request.app.state.z21
    await z21.loco_stop(address)
    return {"ok": True}


@router.post("/{address}/function")
async def function(address: int, cmd: FunctionCommand, request: Request):
    z21 = request.app.state.z21
    await z21.loco_function(address, cmd.function, cmd.state)
    return {"ok": True}


@router.get("/{address}/info")
async def get_info(address: int, request: Request):
    z21 = request.app.state.z21
    await z21.get_loco_info(address)
    return {"ok": True}
