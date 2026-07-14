"""Gemeinsame FastAPI-Dependencies: aktueller Nutzer (Session + S6-Widerruf) und
Trip-Ownership. (C1: aus main.py extrahiert – Verhalten unverändert.)"""

from fastapi import Depends, HTTPException, Request
from sqlmodel import Session

from .db import get_session
from .models import Trip, User
from .security import _allowed


def get_current_user(request: Request, session: Session = Depends(get_session)) -> User:
    uid = request.session.get("uid")
    user = session.get(User, uid) if uid else None
    # Zugang endet sofort, wenn die E-Mail nicht (mehr) freigeschaltet ist.
    if not user or not _allowed(user.email):
        request.session.clear()
        raise HTTPException(401, "Nicht angemeldet")
    # S6: Session-Widerruf – die in der Session signierte token_version muss zur
    # aktuellen passen. Nach Passwortwechsel / "überall abmelden" ist sie erhöht
    # -> alte Cookies gelten nicht mehr (fehlt sie ganz = Alt-Session vor S6).
    if request.session.get("tv") != (user.token_version or 0):
        request.session.clear()
        raise HTTPException(401, "Sitzung abgelaufen – bitte neu anmelden.")
    return user


def _owned_trip(session: Session, trip_id: int, user: User) -> Trip:
    trip = session.get(Trip, trip_id)
    if not trip or trip.user_id != user.id:
        raise HTTPException(404, "Trip nicht gefunden")
    return trip
