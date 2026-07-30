"""RailController – Import von Z21-App-Dateien (.z21)

Format: ZIP mit export/<UUID>/Loco.sqlite (+ Lokbilder).
Getestet gegen Z21-App-Export. Tabellen: vehicles, functions,
control_station_controls, control_station_control_states,
control_station_routes, control_station_route_list.
"""

from fastapi import APIRouter, UploadFile, File, Request, HTTPException
from sqlmodel import Session, select
import io, os, json, zipfile, sqlite3, tempfile, logging

from models.models import Locomotive, Turnout, Route

logger = logging.getLogger("railcontroller.import")
router = APIRouter(prefix="/api/import", tags=["import"])

# Funktionssymbole der Z21-App -> lesbare Labels (Fallback wenn kein shortcut gesetzt)
IMG_LABELS = {
    "light": "Licht", "light2": "Fernlicht", "back_light": "Rücklicht",
    "bugle": "Horn", "sound1": "Sound", "sound2": "Sound", "sound3": "Sound",
    "weight": "Rangiergang",
    "forward_take_power": "Anfahren vorwärts", "backward_take_power": "Anfahren rückwärts",
    "cockpit_light_left": "Führerstand links", "cockpit_light_right": "Führerstand rechts",
}


def _extract_sqlite(raw: bytes) -> bytes:
    if raw[:2] == b"PK":  # ZIP-Archiv
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = z.namelist()
            target = next((n for n in names if n.lower().endswith("loco.sqlite")), None) \
                  or next((n for n in names if n.lower().endswith(".sqlite")), None)
            if not target:
                if any(n.lower().endswith("loco.data") for n in names):
                    raise HTTPException(400, "Neueres XML-Format (loco.data) – noch nicht unterstützt.")
                raise HTTPException(400, f"Keine SQLite-DB im Archiv: {names}")
            return z.read(target)
    if raw[:16].startswith(b"SQLite format 3"):
        return raw
    raise HTTPException(400, "Unbekanntes Format (weder ZIP noch SQLite).")


def _parse(db_bytes: bytes) -> dict:
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
        tmp.write(db_bytes); path = tmp.name
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}

        if "vehicles" not in tables:
            raise HTTPException(400, f"Tabelle 'vehicles' nicht gefunden. Tabellen: {sorted(tables)}")

        # Funktionsnamen je Fahrzeug
        funcs: dict[int, dict] = {}
        if "functions" in tables:
            for r in con.execute("SELECT vehicle_id,function,shortcut,image_name FROM functions"):
                label = (r["shortcut"] or "").strip() or IMG_LABELS.get(r["image_name"])
                if label:
                    funcs.setdefault(r["vehicle_id"], {})[str(r["function"])] = label

        # Loks
        locos = []
        for r in con.execute("SELECT id,name,address FROM vehicles ORDER BY position"):
            if not r["address"]:
                continue
            locos.append({
                "address": r["address"],
                "name": (r["name"] or f"Lok {r['address']}").strip(),
                "function_names": funcs.get(r["id"], {}),
            })

        # Weichen (adressierte Stellpult-Elemente)
        turnouts = {}
        if "control_station_controls" in tables:
            for r in con.execute("SELECT address1 FROM control_station_controls WHERE address1>0"):
                turnouts.setdefault(r["address1"], f"Weiche {r['address1']}")

        # Fahrstraßen
        routes = []
        if {"control_station_routes", "control_station_route_list",
            "control_station_control_states"} <= tables:
            for route in con.execute("SELECT id,name FROM control_station_routes"):
                steps, dropped = [], 0
                for s in con.execute(
                    "SELECT * FROM control_station_route_list WHERE route_id=? ORDER BY position",
                    (route["id"],)
                ):
                    st = con.execute(
                        "SELECT state,address1_value FROM control_station_control_states WHERE id=?",
                        (s["state_id"],)).fetchone()
                    cid = s["control_id"]
                    ctrl = con.execute(
                        "SELECT address1 FROM control_station_controls WHERE id=?",
                        (int(cid),)).fetchone() if cid and str(cid).isdigit() else None
                    # nur binäre Weichenschritte (keine Signale, keine 3-Wege-Stellungen)
                    if not st or not ctrl or not ctrl["address1"] or s["signal_id"] or st["state"] not in (0, 1):
                        dropped += 1
                        continue
                    steps.append({"address": ctrl["address1"], "thrown": bool(st["address1_value"])})
                if steps:
                    routes.append({"name": route["name"] or "Fahrstraße",
                                   "steps": steps, "dropped": dropped})

        con.close()
    finally:
        os.unlink(path)

    return {"locos": locos, "turnouts": turnouts, "routes": routes}


@router.post("/z21")
async def import_z21(request: Request, file: UploadFile = File(...)):
    raw = await file.read()
    parsed = _parse(_extract_sqlite(raw))

    loco_imported = loco_skipped = 0
    turnout_imported = route_imported = 0
    dropped_steps = 0

    with Session(request.app.state.engine) as session:
        # Loks
        for l in parsed["locos"]:
            if session.exec(select(Locomotive).where(Locomotive.address == l["address"])).first():
                loco_skipped += 1
                continue
            session.add(Locomotive(
                address=l["address"], name=l["name"],
                max_speed=127, speed_steps=128,
                function_names=json.dumps(l["function_names"]),
            ))
            loco_imported += 1

        # Weichen
        for addr, name in sorted(parsed["turnouts"].items()):
            if session.exec(select(Turnout).where(Turnout.address == addr)).first():
                continue
            session.add(Turnout(address=addr, name=name))
            turnout_imported += 1

        # Fahrstraßen (mit Nummerierung, da in der App oft namensgleich)
        for i, rt in enumerate(parsed["routes"], 1):
            dropped_steps += rt["dropped"]
            name = f"{rt['name']} #{i}"
            r = Route(name=name)
            r.set_steps(rt["steps"])
            session.add(r)
            route_imported += 1

        session.commit()

    return {
        "ok": True,
        "locos_imported": loco_imported,
        "locos_skipped": loco_skipped,       # doppelte/vorhandene Adressen
        "turnouts_imported": turnout_imported,
        "routes_imported": route_imported,
        "route_steps_dropped": dropped_steps,  # 3-Wege-/Signalschritte
    }