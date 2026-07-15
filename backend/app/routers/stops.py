"""Stopps/POIs einer Reise (anlegen, sortieren, ändern, löschen). (C1)"""

from ..models import _now as _utcnow_naive  # C5: naiv-UTC, nicht deprecated
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..db import get_session
from ..deps import _owned_trip, get_current_user
from ..models import STATUS_VALUES, Stop, StopCreate, StopOrder, StopUpdate, Trip, User

router = APIRouter()


def _validate_status(status: str) -> None:
    if status not in STATUS_VALUES:
        raise HTTPException(
            status_code=422,
            detail=f"status muss eines von {sorted(STATUS_VALUES)} sein",
        )


def _touch_trip(session: Session, trip_id: int) -> None:
    """Markiert die Reise als 'zuletzt beplant' (updated_at) – z.B. wenn ein
    Stopp hinzukommt/sich ändert. Das Frontend wählt darüber die Default-Reise."""
    trip = session.get(Trip, trip_id)
    if trip:
        trip.updated_at = _utcnow_naive()
        session.add(trip)


@router.get("/api/trips/{trip_id}/stops", response_model=List[Stop])
def list_stops(
    trip_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _owned_trip(session, trip_id, user)
    return session.exec(
        select(Stop).where(Stop.trip_id == trip_id).order_by(Stop.reihenfolge, Stop.id)
    ).all()


@router.post("/api/trips/{trip_id}/stops", response_model=Stop, status_code=201)
def create_stop(
    trip_id: int,
    data: StopCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _owned_trip(session, trip_id, user)
    _validate_status(data.status)
    # Neue Stopps ans ENDE der Liste einsortieren (max. reihenfolge + 1),
    # damit ein neuer Übernachtungsplatz unten anhängt statt vorne zu landen.
    orders = session.exec(
        select(Stop.reihenfolge).where(Stop.trip_id == trip_id)
    ).all()
    next_order = (max(orders) + 1) if orders else 0
    stop = Stop.model_validate(data, update={"trip_id": trip_id, "reihenfolge": next_order})
    session.add(stop)
    _touch_trip(session, trip_id)
    session.commit()
    session.refresh(stop)
    from .. import usage
    usage.bump(user.id, "stop_created")
    return stop


@router.put("/api/trips/{trip_id}/stops/order", response_model=List[Stop])
def reorder_stops(
    trip_id: int,
    data: StopOrder,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _owned_trip(session, trip_id, user)
    stops = {
        s.id: s
        for s in session.exec(select(Stop).where(Stop.trip_id == trip_id)).all()
    }
    for index, stop_id in enumerate(data.order):
        stop = stops.get(stop_id)
        if stop:  # unbekannte/fremde IDs werden ignoriert
            stop.reihenfolge = index
            session.add(stop)
    _touch_trip(session, trip_id)
    session.commit()
    return session.exec(
        select(Stop).where(Stop.trip_id == trip_id).order_by(Stop.reihenfolge, Stop.id)
    ).all()


@router.patch("/api/stops/{stop_id}", response_model=Stop)
def update_stop(
    stop_id: int,
    data: StopUpdate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    stop = session.get(Stop, stop_id)
    if not stop:
        raise HTTPException(404, "Stopp nicht gefunden")
    _owned_trip(session, stop.trip_id, user)  # 404, falls fremde Reise
    patch = data.model_dump(exclude_unset=True)
    if "status" in patch and patch["status"] is not None:
        _validate_status(patch["status"])
    for key, value in patch.items():
        setattr(stop, key, value)
    stop.updated_at = _utcnow_naive()
    session.add(stop)
    _touch_trip(session, stop.trip_id)
    session.commit()
    session.refresh(stop)
    return stop


@router.delete("/api/stops/{stop_id}", status_code=204)
def delete_stop(
    stop_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    stop = session.get(Stop, stop_id)
    if not stop:
        raise HTTPException(404, "Stopp nicht gefunden")
    _owned_trip(session, stop.trip_id, user)  # 404, falls fremde Reise
    trip_id = stop.trip_id
    session.delete(stop)
    _touch_trip(session, trip_id)
    session.commit()
