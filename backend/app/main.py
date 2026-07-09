import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import List

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select

from .db import get_session, init_db
from .models import (
    STATUS_VALUES,
    Stop,
    StopCreate,
    StopOrder,
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


@app.get("/api/config")
def config() -> dict:
    # NUR der Render-Key (Maps JavaScript) geht an den Browser – der sollte in der
    # Google-Konsole auf "Maps JavaScript API" + HTTP-Referrer beschränkt sein.
    # Directions/Places/Geocoding laufen server-seitig (siehe unten), damit der
    # exponierte Key nichts Bezahltes auslösen kann.
    return {"googleMapsApiKey": os.getenv("GOOGLE_MAPS_API_KEY", "")}


# ---- Google-Web-Dienste server-seitig (Key erreicht den Browser NIE) ---------
# Server-Key: bevorzugt GOOGLE_MAPS_SERVER_KEY (IP-beschränkt, keine Referrer-
# Beschränkung); Fallback = Render-Key. Wir senden zusätzlich einen Referer-
# Header, damit auch ein referrer-beschränkter Key server-seitig funktioniert
# (Übergangszustand, bis ein dedizierter Server-Key existiert).
_GKEY = os.getenv("GOOGLE_MAPS_SERVER_KEY") or os.getenv("GOOGLE_MAPS_API_KEY", "")
_GREFERER = os.getenv("GOOGLE_KEY_REFERER", "https://camper.dorf27.com/")


def _encode_polyline(points: list) -> str:
    """Google-Polyline-Encoding einer [(lat, lng), …]-Liste (für searchAlongRoute)."""
    out: list = []
    prev_lat = prev_lng = 0
    for lat, lng in points:
        ilat, ilng = int(round(lat * 1e5)), int(round(lng * 1e5))
        for delta in (ilat - prev_lat, ilng - prev_lng):
            delta = ~(delta << 1) if delta < 0 else (delta << 1)
            while delta >= 0x20:
                out.append(chr((0x20 | (delta & 0x1F)) + 63))
                delta >>= 5
            out.append(chr(delta + 63))
        prev_lat, prev_lng = ilat, ilng
    return "".join(out)


async def _gget(url: str, params: dict) -> dict:
    params = {**params, "key": _GKEY}
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(url, params=params, headers={"Referer": _GREFERER})
    return r.json()


@app.post("/api/directions")
async def directions(payload: dict) -> dict:
    """Etappen-Routing (eine Etappe: origin, destination, waypoints[])."""
    if not _GKEY:
        raise HTTPException(503, "Kein Google-Key konfiguriert")
    o, d = payload.get("origin"), payload.get("destination")
    if not o or not d:
        raise HTTPException(422, "origin/destination fehlen")
    params = {
        "origin": f"{o['lat']},{o['lng']}",
        "destination": f"{d['lat']},{d['lng']}",
        "mode": "driving", "language": "de",
    }
    wps = payload.get("waypoints") or []
    if wps:
        params["waypoints"] = "|".join(f"{w['lat']},{w['lng']}" for w in wps)
    data = await _gget("https://maps.googleapis.com/maps/api/directions/json", params)
    routes = data.get("routes") or []
    if data.get("status") != "OK" or not routes:
        return {"ok": False, "status": data.get("status")}
    route = routes[0]
    legs = [
        {"distance": leg["distance"]["value"], "duration": leg["duration"]["value"]}
        for leg in route["legs"]
    ]
    return {"ok": True, "legs": legs, "polyline": route["overview_polyline"]["points"]}


@app.post("/api/places")
async def places(payload: dict) -> dict:
    """Places-Text-Suche; mit `points` (>=2) -> Suche entlang der Route."""
    if not _GKEY:
        raise HTTPException(503, "Kein Google-Key konfiguriert")
    q = (payload.get("textQuery") or "").strip()
    if not q:
        return {"places": []}
    body = {
        "textQuery": q, "languageCode": "de",
        "maxResultCount": int(payload.get("maxResultCount", 8)),
    }
    pts = payload.get("points")
    if pts and len(pts) >= 2:
        enc = _encode_polyline([(p["lat"], p["lng"]) for p in pts])
        body["searchAlongRouteParameters"] = {"polyline": {"encodedPolyline": enc}}
        body["maxResultCount"] = 20
    headers = {
        "X-Goog-Api-Key": _GKEY,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location",
        "Referer": _GREFERER,
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(
            "https://places.googleapis.com/v1/places:searchText", json=body, headers=headers)
    data = r.json()
    out = []
    for p in data.get("places", []):
        loc = p.get("location") or {}
        if loc.get("latitude") is None:
            continue
        out.append({
            "name": (p.get("displayName") or {}).get("text") or "",
            "address": p.get("formattedAddress", ""),
            "lat": loc["latitude"], "lng": loc["longitude"],
        })
    return {"places": out}


@app.get("/api/geocode")
async def geocode(q: str) -> dict:
    """Adresse -> Koordinaten (für Ortssuche + Tour-Start/Ziel)."""
    if not _GKEY:
        raise HTTPException(503, "Kein Google-Key konfiguriert")
    data = await _gget(
        "https://maps.googleapis.com/maps/api/geocode/json", {"address": q, "language": "de"})
    out = []
    for res in data.get("results", []):
        loc = res["geometry"]["location"]
        addr = res.get("formatted_address", "")
        out.append({"name": addr.split(",")[0], "display": addr, "lat": loc["lat"], "lng": loc["lng"]})
    return {"results": out}


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


def _touch_trip(session: Session, trip_id: int) -> None:
    """Markiert die Reise als 'zuletzt beplant' (updated_at) – z.B. wenn ein
    Stopp hinzukommt/sich ändert. Das Frontend wählt darüber die Default-Reise."""
    trip = session.get(Trip, trip_id)
    if trip:
        trip.updated_at = datetime.utcnow()
        session.add(trip)


@app.post("/api/trips/{trip_id}/stops", response_model=Stop, status_code=201)
def create_stop(
    trip_id: int, data: StopCreate, session: Session = Depends(get_session)
):
    if not session.get(Trip, trip_id):
        raise HTTPException(404, "Trip nicht gefunden")
    _validate_status(data.status)
    stop = Stop.model_validate(data, update={"trip_id": trip_id})
    session.add(stop)
    _touch_trip(session, trip_id)
    session.commit()
    session.refresh(stop)
    return stop


@app.put("/api/trips/{trip_id}/stops/order", response_model=List[Stop])
def reorder_stops(
    trip_id: int, data: StopOrder, session: Session = Depends(get_session)
):
    if not session.get(Trip, trip_id):
        raise HTTPException(404, "Trip nicht gefunden")
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
    _touch_trip(session, stop.trip_id)
    session.commit()
    session.refresh(stop)
    return stop


@app.delete("/api/stops/{stop_id}", status_code=204)
def delete_stop(stop_id: int, session: Session = Depends(get_session)):
    stop = session.get(Stop, stop_id)
    if not stop:
        raise HTTPException(404, "Stopp nicht gefunden")
    trip_id = stop.trip_id
    session.delete(stop)
    _touch_trip(session, trip_id)
    session.commit()


# ---- Statisches PWA-Frontend (nach den API-Routen gemountet) -----------------
_frontend = Path(__file__).resolve().parent.parent / "frontend"
if _frontend.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend), html=True), name="frontend")
