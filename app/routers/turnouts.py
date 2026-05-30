"""RailController – Weichen API Router"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select
from pydantic import BaseModel

from models.models import Turnout
from state import app_state

router = APIRouter(prefix="/api/turnouts", tags=["turnouts"])


def get_session(request: Request):
    with Session(request.app.state.engine) as session:
        yield session


class TurnoutCreate(BaseModel):
    address: int
    name: str

class TurnoutCommand(BaseModel):
    thrown: bool


@router.get("/")
def list_turnouts(session: Session = Depends(get_session)):
    turnouts = session.exec(select(Turnout)).all()
    result = []
    for t in turnouts:
        thrown = app_state.turnouts.get(t.address, t.thrown)
        result.append({
            "id": t.id,
            "address": t.address,
            "name": t.name,
            "thrown": thrown,
        })
    return result


@router.post("/")
def create_turnout(data: TurnoutCreate, session: Session = Depends(get_session)):
    existing = session.exec(select(Turnout).where(Turnout.address == data.address)).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Adresse {data.address} bereits vergeben")
    t = Turnout(address=data.address, name=data.name)
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


@router.delete("/{address}")
def delete_turnout(address: int, session: Session = Depends(get_session)):
    t = session.exec(select(Turnout).where(Turnout.address == address)).first()
    if not t:
        raise HTTPException(status_code=404, detail="Weiche nicht gefunden")
    session.delete(t)
    session.commit()
    return {"ok": True}


@router.post("/{address}/set")
async def set_turnout(address: int, cmd: TurnoutCommand, request: Request):
    z21 = request.app.state.z21
    await z21.set_turnout(address, cmd.thrown)
    return {"ok": True}


@router.get("/{address}/info")
async def get_info(address: int, request: Request):
    z21 = request.app.state.z21
    await z21.get_turnout_info(address)
    return {"ok": True}
