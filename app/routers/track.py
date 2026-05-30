"""RailController – Gleisplan API Router"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select
from pydantic import BaseModel
from typing import Optional
import json

from models.models import TrackElement

router = APIRouter(prefix="/api/track", tags=["track"])


def get_session(request: Request):
    with Session(request.app.state.engine) as session:
        yield session


class TrackElementCreate(BaseModel):
    element_type: str
    x: int
    y: int
    rotation: int = 0
    ref_address: Optional[int] = None
    label: str = ""
    properties: dict = {}


class TrackElementUpdate(BaseModel):
    x: Optional[int] = None
    y: Optional[int] = None
    rotation: Optional[int] = None
    label: Optional[str] = None
    ref_address: Optional[int] = None
    properties: Optional[dict] = None


@router.get("/")
def get_track(session: Session = Depends(get_session)):
    elements = session.exec(select(TrackElement)).all()
    return [
        {
            "id": e.id,
            "element_type": e.element_type,
            "x": e.x,
            "y": e.y,
            "rotation": e.rotation,
            "ref_address": e.ref_address,
            "label": e.label,
            "properties": e.get_properties(),
        }
        for e in elements
    ]


@router.post("/")
def add_element(data: TrackElementCreate, session: Session = Depends(get_session)):
    el = TrackElement(
        element_type=data.element_type,
        x=data.x,
        y=data.y,
        rotation=data.rotation,
        ref_address=data.ref_address,
        label=data.label,
        properties=json.dumps(data.properties),
    )
    session.add(el)
    session.commit()
    session.refresh(el)
    return el


@router.put("/{element_id}")
def update_element(element_id: int, data: TrackElementUpdate, session: Session = Depends(get_session)):
    el = session.get(TrackElement, element_id)
    if not el:
        raise HTTPException(status_code=404, detail="Element nicht gefunden")
    if data.x is not None:
        el.x = data.x
    if data.y is not None:
        el.y = data.y
    if data.rotation is not None:
        el.rotation = data.rotation
    if data.label is not None:
        el.label = data.label
    if data.ref_address is not None:
        el.ref_address = data.ref_address
    if data.properties is not None:
        el.properties = json.dumps(data.properties)
    session.add(el)
    session.commit()
    return {"ok": True}


@router.delete("/{element_id}")
def delete_element(element_id: int, session: Session = Depends(get_session)):
    el = session.get(TrackElement, element_id)
    if not el:
        raise HTTPException(status_code=404, detail="Element nicht gefunden")
    session.delete(el)
    session.commit()
    return {"ok": True}


@router.delete("/")
def clear_track(session: Session = Depends(get_session)):
    elements = session.exec(select(TrackElement)).all()
    for el in elements:
        session.delete(el)
    session.commit()
    return {"ok": True, "deleted": len(elements)}
