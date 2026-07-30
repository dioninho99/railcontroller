"""RailController – Fahrstraßen API Router"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select
from pydantic import BaseModel

from models.models import Route

router = APIRouter(prefix="/api/routes", tags=["routes"])


def get_session(request: Request):
    with Session(request.app.state.engine) as session:
        yield session


class RouteStep(BaseModel):
    address: int
    thrown: bool

class RouteCreate(BaseModel):
    name: str
    steps: list[RouteStep] = []


@router.get("/")
def list_routes(session: Session = Depends(get_session)):
    routes = session.exec(select(Route)).all()
    return [{"id": r.id, "name": r.name, "steps": r.get_steps()} for r in routes]


@router.post("/")
def create_route(data: RouteCreate, session: Session = Depends(get_session)):
    r = Route(name=data.name)
    r.set_steps([s.model_dump() for s in data.steps])
    session.add(r); session.commit(); session.refresh(r)
    return {"id": r.id, "name": r.name, "steps": r.get_steps()}


@router.put("/{route_id}")
def update_route(route_id: int, data: RouteCreate, session: Session = Depends(get_session)):
    r = session.get(Route, route_id)
    if not r:
        raise HTTPException(status_code=404, detail="Fahrstraße nicht gefunden")
    r.name = data.name
    r.set_steps([s.model_dump() for s in data.steps])
    session.add(r); session.commit()
    return {"ok": True}


@router.delete("/{route_id}")
def delete_route(route_id: int, session: Session = Depends(get_session)):
    r = session.get(Route, route_id)
    if not r:
        raise HTTPException(status_code=404, detail="Fahrstraße nicht gefunden")
    session.delete(r); session.commit()
    return {"ok": True}


@router.post("/{route_id}/execute")
async def execute_route(route_id: int, request: Request):
    # Schritte lesen, Session sofort schließen, dann Hardware ansteuern
    with Session(request.app.state.engine) as session:
        r = session.get(Route, route_id)
        if not r:
            raise HTTPException(status_code=404, detail="Fahrstraße nicht gefunden")
        steps = r.get_steps()

    z21 = request.app.state.z21
    for step in steps:
        await z21.set_turnout(step["address"], step["thrown"])  # wartet intern ~0.35s
    return {"ok": True, "steps": len(steps)}