"""RailController – Import von Z21-App-Dateien (.z21)

Format: ZIP mit export/<UUID>/Loco.sqlite. Importiert Loks, Weichen,
Fahrstraßen und den Gleisplan (Seite mit den meisten Elementen).
Mehrfach-Import ist sicher: Loks/Weichen/Routen werden übersprungen,
der Gleisplan wird ersetzt.

Gleisplan-Rekonstruktion:
  * type=0 sind GERADEN (keine Leerzellen) – werden importiert.
  * Z21-Winkel 90/270 = waagrecht, 0/180 = senkrecht. In track.html ist
    eine Gerade bei rotation 0 waagrecht -> rotation = (angle+90) % 180
    (deckt auch die 45°-Diagonalen ab).
  * Kurven (type 28): Drehung per gewichteter Best-Fit-Suche – gewählt wird
    die Orientierung, deren beide Bogen-Enden am besten zu belegten Nachbarn
    passen (Diagonal-Nachbarn zählen halb). Der Z21-Winkel dient nur als
    Tiebreaker. Reine Diagonalläufe ohne Kardinal-Nachbar werden als
    diagonale Gerade dargestellt.
"""

from fastapi import APIRouter, UploadFile, File, Request, HTTPException
from sqlmodel import Session, select
import io, os, json, zipfile, sqlite3, tempfile, logging

from models.models import Locomotive, Turnout, Route, TrackElement

logger = logging.getLogger("railcontroller.import")
router = APIRouter(prefix="/api/import", tags=["import"])

IMG_LABELS = {
    "light": "Licht", "light2": "Fernlicht", "back_light": "Rücklicht",
    "bugle": "Horn", "sound1": "Sound", "sound2": "Sound", "sound3": "Sound",
    "weight": "Rangiergang",
    "forward_take_power": "Anfahren vorwärts", "backward_take_power": "Anfahren rückwärts",
    "cockpit_light_left": "Führerstand links", "cockpit_light_right": "Führerstand rechts",
}


# ── Gleisplan-Geometrie ────────────────────────────────────────────

_DIRV = {"N": (0, -1), "E": (1, 0), "S": (0, 1), "W": (-1, 0)}          # Kardinal-Nachbarn
_DIAGV = {"NE": (1, -1), "SE": (1, 1), "SW": (-1, 1), "NW": (-1, -1)}   # Diagonal-Nachbarn
_DIAG_COMP = {"NE": ("N", "E"), "SE": ("S", "E"), "SW": ("S", "W"), "NW": ("N", "W")}
# track.html-Kurve bei rotation r verbindet diese zwei Seiten:
_SIDES = {0: ("W", "N"), 90: ("N", "E"), 180: ("E", "S"), 270: ("S", "W")}
# Tiebreaker: Kurvenwinkel -> bevorzugte Drehung
_CURVE_ANGLE = {0: 0, 45: 270, 90: 90, 135: 0, 180: 180, 225: 0, 270: 270, 315: 180}


def _occ(occ, x, y, d):
    dx, dy = _DIRV[d]
    return (x + dx, y + dy) in occ


def _diag_axis(occ, x, y):
    ne = (x + 1, y - 1) in occ; sw = (x - 1, y + 1) in occ
    se = (x + 1, y + 1) in occ; nw = (x - 1, y - 1) in occ
    if (ne or sw) and not (se or nw): return 135   # "/"
    if (se or nw) and not (ne or sw): return 45    # "\"
    return None


def _reconstruct(cell: dict, occ: set):
    """Z21-Zelle -> (element_type, rotation) für track.html."""
    t = cell["type"]
    a = int(cell["angle"]) % 360
    x, y = cell["x"], cell["y"]

    if t in (1, 2, 3):
        return "turnout", (a + 90) % 360
    if t in (10, 12, 23):
        return "signal", (a + 90) % 360

    if t == 28:  # Kurve
        card_occ = [d for d in _DIRV if _occ(occ, x, y, d)]
        # reiner Diagonallauf ohne kardinalen Nachbar -> diagonale Gerade
        if not card_occ:
            da = _diag_axis(occ, x, y)
            if da is not None:
                return "straight", da
        # belegte Diagonalen in Kardinal-Komponenten zerlegen (halbes Gewicht)
        comp = set()
        for k, (dx, dy) in _DIAGV.items():
            if (x + dx, y + dy) in occ:
                comp.update(_DIAG_COMP[k])
        best, best_score = 0, -1.0
        for r, (p, q) in _SIDES.items():
            score = 0.0
            for side in (p, q):
                if _occ(occ, x, y, side):   score += 1.0
                elif side in comp:          score += 0.5
            if score > best_score or (score == best_score and r == _CURVE_ANGLE.get(a, -1)):
                best, best_score = r, score
        return "curve", best

    # type 0, 39 und übrige Gleisstücke -> Gerade
    return "straight", (a + 90) % 180


def _extract_sqlite(raw: bytes) -> bytes:
    if raw[:2] == b"PK":
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
            locos.append({"address": r["address"],
                          "name": (r["name"] or f"Lok {r['address']}").strip(),
                          "function_names": funcs.get(r["id"], {})})

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
                    (route["id"],)):
                    st = con.execute(
                        "SELECT state,address1_value FROM control_station_control_states WHERE id=?",
                        (s["state_id"],)).fetchone()
                    cid = s["control_id"]
                    ctrl = con.execute(
                        "SELECT address1 FROM control_station_controls WHERE id=?",
                        (int(cid),)).fetchone() if cid and str(cid).isdigit() else None
                    if not st or not ctrl or not ctrl["address1"] or s["signal_id"] or st["state"] not in (0, 1):
                        dropped += 1; continue
                    steps.append({"address": ctrl["address1"], "thrown": bool(st["address1_value"])})
                if steps:
                    routes.append({"name": route["name"] or "Fahrstraße",
                                   "steps": steps, "dropped": dropped})

        # Gleisplan: Seite mit den meisten Elementen (type=0 zählt jetzt mit)
        track, track_page = [], None
        if "control_station_controls" in tables:
            page = con.execute(
                "SELECT page_id FROM control_station_controls "
                "GROUP BY page_id ORDER BY COUNT(*) DESC LIMIT 1").fetchone()
            if page:
                pid = page["page_id"]
                pn = con.execute("SELECT name FROM control_station_pages WHERE id=?", (pid,)).fetchone()
                track_page = pn["name"] if pn else str(pid)
                cells = [dict(r) for r in con.execute(
                    "SELECT x,y,angle,type,address1 FROM control_station_controls WHERE page_id=?", (pid,))]
                occ = {(c["x"], c["y"]) for c in cells}
                if cells:
                    minx = min(c["x"] for c in cells); miny = min(c["y"] for c in cells)
                    for c in cells:
                        et, rot = _reconstruct(c, occ)
                        track.append({
                            "element_type": et,
                            "x": (c["x"] - minx) * 40,
                            "y": (c["y"] - miny) * 40,
                            "rotation": rot,
                            "ref_address": c["address1"] or None,
                        })
        con.close()
    finally:
        os.unlink(path)

    return {"locos": locos, "turnouts": turnouts, "routes": routes,
            "track": track, "track_page": track_page}


@router.post("/z21")
async def import_z21(request: Request, file: UploadFile = File(...)):
    raw = await file.read()
    parsed = _parse(_extract_sqlite(raw))

    loco_imported = loco_skipped = turnout_imported = 0
    route_imported = dropped_steps = track_imported = 0

    with Session(request.app.state.engine) as session:
        # Loks (vorhandene Adressen überspringen)
        for l in parsed["locos"]:
            if session.exec(select(Locomotive).where(Locomotive.address == l["address"])).first():
                loco_skipped += 1; continue
            session.add(Locomotive(address=l["address"], name=l["name"],
                                   max_speed=127, speed_steps=128,
                                   function_names=json.dumps(l["function_names"])))
            loco_imported += 1

        # Weichen
        for addr, name in sorted(parsed["turnouts"].items()):
            if session.exec(select(Turnout).where(Turnout.address == addr)).first():
                continue
            session.add(Turnout(address=addr, name=name)); turnout_imported += 1

        # Fahrstraßen (nach Name deduplizieren -> Re-Import sicher)
        for i, rt in enumerate(parsed["routes"], 1):
            dropped_steps += rt["dropped"]
            name = f"{rt['name']} #{i}"
            if session.exec(select(Route).where(Route.name == name)).first():
                continue
            r = Route(name=name); r.set_steps(rt["steps"])
            session.add(r); route_imported += 1

        # Gleisplan (ersetzt vorhandenen Plan komplett)
        if parsed["track"]:
            for el in session.exec(select(TrackElement)).all():
                session.delete(el)
            for e in parsed["track"]:
                session.add(TrackElement(
                    element_type=e["element_type"], x=e["x"], y=e["y"],
                    rotation=e["rotation"], ref_address=e["ref_address"],
                    label="", properties="{}"))
                track_imported += 1

        session.commit()

    return {"ok": True,
            "locos_imported": loco_imported, "locos_skipped": loco_skipped,
            "turnouts_imported": turnout_imported,
            "routes_imported": route_imported, "route_steps_dropped": dropped_steps,
            "track_imported": track_imported, "track_page": parsed["track_page"]}