"""Reisen (Trips) inkl. CSV-Export. Alles user-scoped (Fremdzugriff -> 404). (C1)"""

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from ..csv_export import _csv_download, _csv_slug, _stops_csv
from ..db import get_session
from ..deps import _owned_trip, get_current_user
from ..models import Stop, Trip, TripCreate, TripUpdate, User

router = APIRouter()


@router.get("/api/trips", response_model=List[Trip])
def list_trips(user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    return session.exec(
        select(Trip).where(Trip.user_id == user.id).order_by(Trip.start_datum, Trip.id)
    ).all()


@router.get("/api/export.csv")
def export_csv(user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    """Alle eigenen Reisen als CSV (user-scoped)."""
    trips = session.exec(
        select(Trip).where(Trip.user_id == user.id).order_by(Trip.start_datum, Trip.id)
    ).all()
    return _csv_download(_stops_csv(session, trips), "camper-reisen.csv")


@router.get("/api/trips/{trip_id}/export.csv")
def export_trip_csv(
    trip_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Eine einzelne eigene Reise als CSV (Fremdzugriff -> 404)."""
    trip = _owned_trip(session, trip_id, user)
    return _csv_download(_stops_csv(session, [trip]), f"camper-{_csv_slug(trip.name)}.csv")


@router.post("/api/trips", response_model=Trip, status_code=201)
def create_trip(
    data: TripCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    trip = Trip.model_validate(data, update={"user_id": user.id})
    session.add(trip)
    session.commit()
    session.refresh(trip)
    from .. import usage
    usage.bump(user.id, "trip_created")
    return trip


@router.get("/api/trips/{trip_id}", response_model=Trip)
def get_trip(
    trip_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return _owned_trip(session, trip_id, user)


@router.patch("/api/trips/{trip_id}", response_model=Trip)
def update_trip(
    trip_id: int,
    data: TripUpdate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    trip = _owned_trip(session, trip_id, user)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(trip, key, value)
    trip.updated_at = datetime.utcnow()
    session.add(trip)
    session.commit()
    session.refresh(trip)
    return trip


@router.delete("/api/trips/{trip_id}", status_code=204)
def delete_trip(
    trip_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    trip = _owned_trip(session, trip_id, user)
    for stop in session.exec(select(Stop).where(Stop.trip_id == trip_id)).all():
        session.delete(stop)
    session.delete(trip)
    session.commit()
