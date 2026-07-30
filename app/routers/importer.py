"""RailController – Import von Z21-App-Dateien (.z21)"""

from fastapi import APIRouter, UploadFile, File, Request, HTTPException
from sqlmodel import Session, select
import io, os, json, zipfile, sqlite3, tempfile, logging

from models.models import Locomotive

logger = logging.getLogger("railcontroller.import")
router = APIRouter(prefix="/api/import", tags=["import"])


def _pick(cols, candidates, contains=None):
    lower = {c.lower(): c for c in cols}
    for cand in candidates:
        if cand in lower:
            return lower[cand]
    if contains:
        for c in cols:
            if all(part in c.lower() for part in contains):
                return c
    return None


def _extract_sqlite(raw: bytes) -> bytes:
    if raw[:2] == b"PK":  # ZIP
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


@router.post("/z21")
async def import_z21(request: Request, file: UploadFile = File(...)):
    raw = await file.read()
    db_bytes = _extract_sqlite(raw)

    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
        tmp.write(db_bytes); tmp_path = tmp.name

    try:
        con = sqlite3.connect(f"file:{tmp_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]

        if "vehicles" not in tables:
            raise HTTPException(400, f"Tabelle 'vehicles' nicht gefunden. Tabellen: {tables}")

        vcols = [r["name"] for r in con.execute("PRAGMA table_info(vehicles)").fetchall()]
        addr_col  = _pick(vcols, ["address","addr","locoaddress","dccaddress","loco_address"], ["addr"])
        name_col  = _pick(vcols, ["name","title","loconame","description"], ["name"])
        speed_col = _pick(vcols, ["maxspeed","vmax","max_speed"], ["max","speed"])
        if not addr_col:
            raise HTTPException(400, f"Keine Adress-Spalte erkannt. Spalten: {vcols}")

        rows = con.execute("SELECT rowid, * FROM vehicles").fetchall()

        func_map = {}
        if "functions" in tables:
            try:
                fcols = [r["name"] for r in con.execute("PRAGMA table_info(functions)").fetchall()]
                veh_col   = _pick(fcols, ["vehicle_id","loco_id","parent_id","vehicleid"], ["vehicle"])
                num_col   = _pick(fcols, ["shortcut","button","position","index","number","nr"], ["button"])
                fname_col = _pick(fcols, ["name","title","text","label"], ["name"])
                if veh_col and num_col and fname_col:
                    for fr in con.execute(f'SELECT "{veh_col}","{num_col}","{fname_col}" FROM functions').fetchall():
                        if fr[fname_col]:
                            func_map.setdefault(fr[veh_col], {})[str(int(fr[num_col]))] = str(fr[fname_col])
            except Exception as e:
                logger.warning(f"Funktions-Import übersprungen: {e}")

        con.close()
    finally:
        os.unlink(tmp_path)

    imported = skipped = 0
    with Session(request.app.state.engine) as session:
        for r in rows:
            try:
                address = int(r[addr_col])
            except (TypeError, ValueError):
                continue
            if not address:
                continue
            if session.exec(select(Locomotive).where(Locomotive.address == address)).first():
                skipped += 1; continue
            name = str(r[name_col]).strip() if name_col and r[name_col] else f"Lok {address}"
            max_speed = 127
            if speed_col and r[speed_col]:
                try:
                    v = int(r[speed_col])
                    if 1 <= v <= 127: max_speed = v
                except (TypeError, ValueError):
                    pass
            fnames = func_map.get(r["rowid"], {}) or func_map.get(address, {})
            session.add(Locomotive(address=address, name=name, max_speed=max_speed,
                                   function_names=json.dumps(fnames)))
            imported += 1
        session.commit()

    return {"ok": True, "imported": imported, "skipped": skipped,
            "tables": tables, "detected_columns": [c for c in [addr_col, name_col, speed_col] if c]}