"""Health + Client-Config (Render-Key). (C1)"""

import os

from fastapi import APIRouter, Depends

from .. import __version__
from ..deps import get_current_user
from ..models import User

router = APIRouter()


@router.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/api/config")
def config(user: User = Depends(get_current_user)) -> dict:
    # NUR der Render-Key (Maps JavaScript) geht an den Browser – der sollte in der
    # Google-Konsole auf "Maps JavaScript API" + HTTP-Referrer beschränkt sein.
    # Directions/Places/Geocoding laufen server-seitig (siehe unten), damit der
    # exponierte Key nichts Bezahltes auslösen kann.
    return {"googleMapsApiKey": os.getenv("GOOGLE_MAPS_API_KEY", ""), "version": __version__}
