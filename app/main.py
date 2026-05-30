"""
RailController – FastAPI Hauptapplikation
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from sqlmodel import create_engine, Session, select

from models.models import Locomotive, Turnout, create_db_and_tables, seed_demo_data
from state import app_state
from routers import locos, turnouts, track

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "info").upper(),
    format="%(asctime)s [%(name)s] %(levelname)s - %(message)s",
)
logger = logging.getLogger("railcontroller.main")

# ── Konfiguration ─────────────────────────────

Z21_HOST = os.getenv("Z21_HOST", "192.168.0.111")
Z21_PORT = int(os.getenv("Z21_PORT", "21105"))
Z21_MOCK = os.getenv("Z21_MOCK", "false").lower() == "true"
_DB_URL  = os.getenv("DATABASE_URL", "sqlite:////data/railcontroller.db")


def _resolve_db_url(url: str) -> str:
    """Stellt sicher dass das DB-Verzeichnis existiert. Fallback auf lokale DB."""
    if not url.startswith("sqlite:///"):
        return url
    # sqlite:////data/x.db -> /data/x.db
    path = url[len("sqlite:///"):]
    directory = os.path.dirname(path)
    if not directory:
        return url
    try:
        os.makedirs(directory, exist_ok=True)
        logger.info(f"DB-Verzeichnis bereit: {directory}")
        return url
    except OSError as e:
        fallback = "sqlite:///./railcontroller.db"
        logger.warning(f"DB-Verzeichnis '{directory}' nicht erstellbar ({e}), Fallback: {fallback}")
        return fallback


DB_URL = _resolve_db_url(_DB_URL)


# ── Startup / Shutdown ────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = create_engine(DB_URL, echo=False)
    create_db_and_tables(engine)
    app.state.engine = engine

    # Z21 Client (echt oder Mock)
    if Z21_MOCK:
        logger.warning("MOCK-MODUS aktiv - kein echtes Z21-Geraet")
        from mock_z21 import MockZ21Client
        z21 = MockZ21Client()
    else:
        from z21.client import Z21Client
        z21 = Z21Client(Z21_HOST, Z21_PORT)
        logger.info(f"Z21 Client konfiguriert: {Z21_HOST}:{Z21_PORT}")

    z21.add_callback(app_state.handle_z21_event)
    app.state.z21 = z21

    if Z21_MOCK:
        seed_demo_data(engine)

    try:
        await z21.connect()
        app_state.z21_connected = True
    except Exception as e:
        logger.error(f"Z21 Verbindung fehlgeschlagen: {e}")
        app_state.z21_connected = False

    yield

    await z21.disconnect()
    logger.info("RailController beendet")


# ── FastAPI App ───────────────────────────────

app = FastAPI(
    title="RailController",
    description="Z21 Modellbahn-Steuerung",
    version="0.1.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

app.include_router(locos.router)
app.include_router(turnouts.router)
app.include_router(track.router)


# ── System-API ────────────────────────────────

@app.get("/api/system")
async def system_status():
    return app_state.full_state()


@app.post("/api/system/power/on")
async def power_on(request: Request):
    await request.app.state.z21.track_power_on()
    return {"ok": True}


@app.post("/api/system/power/off")
async def power_off(request: Request):
    await request.app.state.z21.track_power_off()
    return {"ok": True}


@app.post("/api/system/emergency_stop")
async def estop(request: Request):
    await request.app.state.z21.emergency_stop()
    return {"ok": True}


# ── WebSocket ─────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    await app_state.register_ws(websocket)
    logger.info(f"WebSocket verbunden: {websocket.client}")

    try:
        import json
        await websocket.send_text(json.dumps(app_state.full_state()))
    except Exception:
        pass

    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text('{"type":"pong"}')
    except WebSocketDisconnect:
        pass
    finally:
        await app_state.unregister_ws(websocket)
        logger.info(f"WebSocket getrennt: {websocket.client}")


# ── HTML Seiten ───────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/locos", response_class=HTMLResponse)
async def locos_page(request: Request):
    return templates.TemplateResponse("locos.html", {"request": request})


@app.get("/track", response_class=HTMLResponse)
async def track_page(request: Request):
    return templates.TemplateResponse("track.html", {"request": request})


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse("settings.html", {"request": request})
