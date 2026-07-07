from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import List

from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select

from .db import get_session, init_db
from .models import (
    STATUS_VALUES,
    Stop,
    StopCreate,
    StopUpdate,
    Trip,
    TripCreate,
    TripUpdate,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Camper-Reiseplaner", version="1.0.0", lifespan=lifespan)


def _validate_status(status: str) -> None:
    if status not in STATUS_VALUES:
        raise HTTPException(
            status_code=422,
            detail=f"status muss eines von {sorted(STATUS_VALUES)} sein",
        )


# ---- Health ------------------------------------------------------------------
@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# ---- Trips -------------------------------------------------------------------
@app.get("/api/trips", response_model=List[Trip])
def list_trips(session: Session = Depends(get_session)):
    return session.exec(select(Trip).order_by(Trip.start_datum, Trip.id)).all()


@app.post("/api/trips", response_model=Trip, status_code=201)
def create_trip(data: TripCreate, session: Session = Depends(get_session)):
    trip = Trip.model_validate(data)
    session.add(trip)
    session.commit()
    session.refresh(trip)
    return trip


@app.get("/api/trips/{trip_id}", response_model=Trip)
def get_trip(trip_id: int, session: Session = Depends(get_session)):
    trip = session.get(Trip, trip_id)
    if not trip:
        raise HTTPException(404, "Trip nicht gefunden")
    return trip


@app.patch("/api/trips/{trip_id}", response_model=Trip)
def update_trip(
    trip_id: int, data: TripUpdate, session: Session = Depends(get_session)
):
    trip = session.get(Trip, trip_id)
    if not trip:
        raise HTTPException(404, "Trip nicht gefunden")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(trip, key, value)
    trip.updated_at = datetime.utcnow()
    session.add(trip)
    session.commit()
    session.refresh(trip)
    return trip


@app.delete("/api/trips/{trip_id}", status_code=204)
def delete_trip(trip_id: int, session: Session = Depends(get_session)):
    trip = session.get(Trip, trip_id)
    if not trip:
        raise HTTPException(404, "Trip nicht gefunden")
    for stop in session.exec(select(Stop).where(Stop.trip_id == trip_id)).all():
        session.delete(stop)
    session.delete(trip)
    session.commit()


# ---- Stops -------------------------------------------------------------------
@app.get("/api/trips/{trip_id}/stops", response_model=List[Stop])
def list_stops(trip_id: int, session: Session = Depends(get_session)):
    if not session.get(Trip, trip_id):
        raise HTTPException(404, "Trip nicht gefunden")
    return session.exec(
        select(Stop).where(Stop.trip_id == trip_id).order_by(Stop.reihenfolge, Stop.id)
    ).all()


@app.post("/api/trips/{trip_id}/stops", response_model=Stop, status_code=201)
def create_stop(
    trip_id: int, data: StopCreate, session: Session = Depends(get_session)
):
    if not session.get(Trip, trip_id):
        raise HTTPException(404, "Trip nicht gefunden")
    _validate_status(data.status)
    stop = Stop.model_validate(data, update={"trip_id": trip_id})
    session.add(stop)
    session.commit()
    session.refresh(stop)
    return stop


@app.patch("/api/stops/{stop_id}", response_model=Stop)
def update_stop(
    stop_id: int, data: StopUpdate, session: Session = Depends(get_session)
):
    stop = session.get(Stop, stop_id)
    if not stop:
        raise HTTPException(404, "Stopp nicht gefunden")
    patch = data.model_dump(exclude_unset=True)
    if "status" in patch and patch["status"] is not None:
        _validate_status(patch["status"])
    for key, value in patch.items():
        setattr(stop, key, value)
    stop.updated_at = datetime.utcnow()
    session.add(stop)
    session.commit()
    session.refresh(stop)
    return stop


@app.delete("/api/stops/{stop_id}", status_code=204)
def delete_stop(stop_id: int, session: Session = Depends(get_session)):
    stop = session.get(Stop, stop_id)
    if not stop:
        raise HTTPException(404, "Stopp nicht gefunden")
    session.delete(stop)
    session.commit()


# ---- Statisches PWA-Frontend (nach den API-Routen gemountet) -----------------
_frontend = Path(__file__).resolve().parent.parent / "frontend"
if _frontend.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend), html=True), name="frontend")
