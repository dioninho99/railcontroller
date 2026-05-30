"""
RailController – Datenmodelle
SQLModel-Modelle für Loks, Weichen, Gleisplan-Elemente
"""

from typing import Optional
from sqlmodel import Field, SQLModel, create_engine, Session, select
import json


# ──────────────────────────────────────────────
# Datenbank-Modelle
# ──────────────────────────────────────────────

class Locomotive(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    address: int = Field(index=True, unique=True)
    name: str
    icon: str = "🚂"
    max_speed: int = 127
    speed_steps: int = 128
    # Funktionsnamen als JSON gespeichert: {"0": "Licht", "1": "Horn", ...}
    function_names: str = Field(default="{}")

    def get_function_names(self) -> dict:
        try:
            return json.loads(self.function_names)
        except Exception:
            return {}

    def set_function_names(self, names: dict):
        self.function_names = json.dumps(names)


class Turnout(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    address: int = Field(index=True, unique=True)
    name: str
    thrown: bool = False  # False = gerade, True = abbiegen


class TrackElement(SQLModel, table=True):
    """Gleisplan-Element (gespeichert als JSON-Blob pro Element)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    element_type: str   # "straight", "curve", "turnout", "signal", "block", "label"
    x: int
    y: int
    rotation: int = 0
    ref_address: Optional[int] = None  # Weichen-/Signal-Adresse
    label: str = ""
    properties: str = Field(default="{}")  # Zusatz-Properties als JSON

    def get_properties(self) -> dict:
        try:
            return json.loads(self.properties)
        except Exception:
            return {}


# ──────────────────────────────────────────────
# Datenbank-Setup
# ──────────────────────────────────────────────

def create_db_and_tables(engine):
    SQLModel.metadata.create_all(engine)


def seed_demo_data(engine):
    """Demo-Daten für Mock-Modus einfügen."""
    with Session(engine) as session:
        if session.exec(select(Locomotive)).first():
            return  # bereits vorhanden

        locos = [
            Locomotive(
                address=3,
                name="BR 103",
                icon="🚂",
                max_speed=127,
                function_names=json.dumps({
                    "0": "Licht", "1": "Horn", "2": "Fahrgeräusch",
                    "3": "Pfeife", "4": "Ansage"
                })
            ),
            Locomotive(
                address=5,
                name="ICE",
                icon="🚄",
                max_speed=127,
                function_names=json.dumps({
                    "0": "Licht", "1": "Horn", "2": "Fahrgeräusch"
                })
            ),
            Locomotive(
                address=10,
                name="Rangierlok",
                icon="🚃",
                max_speed=80,
                function_names=json.dumps({
                    "0": "Licht", "1": "Rangierhorn"
                })
            ),
        ]
        turnouts = [
            Turnout(address=1, name="Weiche W1"),
            Turnout(address=2, name="Weiche W2"),
            Turnout(address=3, name="Weiche W3"),
        ]
        for l in locos:
            session.add(l)
        for t in turnouts:
            session.add(t)
        session.commit()
