from datetime import date, datetime
from typing import Optional

from sqlmodel import Field, SQLModel

# Erlaubte Stopp-Status (Marker-Farbe im Frontend danach)
STATUS_VALUES = {"geplant", "besucht", "reserviert"}


def _now() -> datetime:
    return datetime.utcnow()


# ---- Tabellen ----------------------------------------------------------------
class Trip(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    beschreibung: Optional[str] = None
    start_datum: Optional[date] = None
    end_datum: Optional[date] = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Stop(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    trip_id: int = Field(foreign_key="trip.id", index=True)
    name: str
    lat: float
    lng: float
    status: str = "geplant"
    notiz: Optional[str] = None
    datum: Optional[date] = None
    reihenfolge: int = 0
    reserviert: bool = False
    reserviert_von: Optional[datetime] = None
    reserviert_bis: Optional[datetime] = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


# ---- Ein-/Ausgabe-Schemata ---------------------------------------------------
class TripCreate(SQLModel):
    name: str
    beschreibung: Optional[str] = None
    start_datum: Optional[date] = None
    end_datum: Optional[date] = None


class TripUpdate(SQLModel):
    name: Optional[str] = None
    beschreibung: Optional[str] = None
    start_datum: Optional[date] = None
    end_datum: Optional[date] = None


class StopCreate(SQLModel):
    name: str
    lat: float
    lng: float
    status: str = "geplant"
    notiz: Optional[str] = None
    datum: Optional[date] = None
    reihenfolge: int = 0
    reserviert: bool = False
    reserviert_von: Optional[datetime] = None
    reserviert_bis: Optional[datetime] = None


class StopUpdate(SQLModel):
    name: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    status: Optional[str] = None
    notiz: Optional[str] = None
    datum: Optional[date] = None
    reihenfolge: Optional[int] = None
    reserviert: Optional[bool] = None
    reserviert_von: Optional[datetime] = None
    reserviert_bis: Optional[datetime] = None
