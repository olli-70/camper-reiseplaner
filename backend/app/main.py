import hashlib
import json
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import List

import bcrypt
import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from sqlmodel import Session, select

from . import __version__
from .db import engine, get_session, init_db
from .models import (
    STATUS_VALUES,
    Stop,
    StopCreate,
    StopOrder,
    StopUpdate,
    Trip,
    TripCreate,
    TripUpdate,
    User,
)

# ---- Auth-Grundlagen ---------------------------------------------------------
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _members() -> dict:
    """Einladungsliste aus ENV MEMBERS (JSON: [{email, code}, …]) -> {email: code}.
    Quelle ist Vault-Feld `member`; das Playbook reicht sie als JSON durch."""
    try:
        raw = json.loads(os.getenv("MEMBERS", "[]"))
        return {
            (m.get("email") or "").strip().lower(): (m.get("code") or "").strip()
            for m in raw
            if m.get("email") and m.get("code")
        }
    except Exception:
        return {}


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _admin_email() -> str:
    return os.getenv("ADMIN_USER", "").strip().lower()


def _allowed(email: str) -> bool:
    """Nur E-Mails aus der Vault-`member`-Liste ODER die Admin-E-Mail dürfen rein."""
    email = (email or "").strip().lower()
    return bool(email) and (email == _admin_email() or email in _members())


def hash_password(pw: str) -> str:
    # bcrypt begrenzt auf 72 Byte; längere Passwörter werden abgeschnitten.
    return bcrypt.hashpw(pw.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode("utf-8")[:72], hashed.encode("utf-8"))
    except Exception:
        return False


def _seed_admin() -> None:
    """Admin-Konto aus ENV pflegen: Passwort = Vault (source of truth); wenn die
    Admin-E-Mail sich geändert hat, das bestehende Admin-Konto UMBENENNEN (Reisen
    bleiben erhalten). Verwaiste Reisen (user_id NULL) dem Admin zuordnen."""
    email = _admin_email()
    pw = os.getenv("ADMIN_PASSWORD", "")
    if not email or not pw:
        return
    with Session(engine) as session:
        admin = session.exec(select(User).where(User.email == email)).first()
        if not admin:
            # evtl. existiert der Admin noch unter der alten E-Mail -> umbenennen
            admin = session.exec(
                select(User).where(User.is_admin == True)).first()  # noqa: E712
            if admin:
                admin.email = email
            elif session.exec(select(User)).first():
                return  # Nicht-Admin-Nutzer existieren -> keinen Admin anlegen
            else:
                admin = User(email=email, password_hash="", is_admin=True)
                session.add(admin)
        admin.is_admin = True
        admin.password_hash = hash_password(pw)  # Admin-Passwort folgt Vault
        session.add(admin)
        session.commit()
        session.refresh(admin)
        orphans = session.exec(select(Trip).where(Trip.user_id == None)).all()  # noqa: E711
        for t in orphans:
            t.user_id = admin.id
            session.add(t)
        if orphans:
            session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _seed_admin()
    yield


app = FastAPI(title="Camper-Reiseplaner", version=__version__, lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET", "dev-insecure-change-me"),
    same_site="lax",
    https_only=os.getenv("COOKIE_SECURE", "1") != "0",
    max_age=60 * 60 * 24 * 30,  # 30 Tage
)


def get_current_user(request: Request, session: Session = Depends(get_session)) -> User:
    uid = request.session.get("uid")
    user = session.get(User, uid) if uid else None
    # Zugang endet sofort, wenn die E-Mail nicht (mehr) freigeschaltet ist.
    if not user or not _allowed(user.email):
        request.session.clear()
        raise HTTPException(401, "Nicht angemeldet")
    return user


def _owned_trip(session: Session, trip_id: int, user: User) -> Trip:
    trip = session.get(Trip, trip_id)
    if not trip or trip.user_id != user.id:
        raise HTTPException(404, "Trip nicht gefunden")
    return trip


# ---- Login-Ratenbegrenzung (einfach, pro IP) --------------------------------
_login_hits: dict = {}
_RL_MAX = int(os.getenv("LOGIN_RATELIMIT", "20"))  # Versuche pro 5-Min-Fenster/IP


def _rate_limit(request: Request) -> None:
    ip = request.client.host if request.client else "?"
    now = time.time()
    hits = [t for t in _login_hits.get(ip, []) if now - t < 300]  # 5-Min-Fenster
    if len(hits) >= _RL_MAX:
        raise HTTPException(429, "Zu viele Versuche, bitte kurz warten.")
    hits.append(now)
    _login_hits[ip] = hits


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
def config(user: User = Depends(get_current_user)) -> dict:
    # NUR der Render-Key (Maps JavaScript) geht an den Browser – der sollte in der
    # Google-Konsole auf "Maps JavaScript API" + HTTP-Referrer beschränkt sein.
    # Directions/Places/Geocoding laufen server-seitig (siehe unten), damit der
    # exponierte Key nichts Bezahltes auslösen kann.
    return {"googleMapsApiKey": os.getenv("GOOGLE_MAPS_API_KEY", ""), "version": __version__}


# ---- Authentifizierung (E-Mail + Passwort, Session-Cookie) -------------------
@app.post("/api/auth/set-password")
def set_password(payload: dict, request: Request, session: Session = Depends(get_session)):
    """Passwort per persönlichem Einmalcode setzen (Erst-Anmeldung ODER Reset).
    Der Code stammt aus der Vault-`member`-Liste (email->code) und ist EINMALIG:
    nach Benutzung ungültig, bis ein neuer Code hinterlegt wird."""
    _rate_limit(request)
    email = (payload.get("email") or "").strip().lower()
    code = (payload.get("code") or "").strip()
    pw = payload.get("password") or ""
    members = _members()
    if not code or email not in members or members[email] != code:
        raise HTTPException(403, "E-Mail und Einmalcode passen nicht (oder Code abgelaufen).")
    if len(pw) < 8:
        raise HTTPException(422, "Passwort muss mindestens 8 Zeichen haben.")
    ch = _code_hash(code)
    user = session.exec(select(User).where(User.email == email)).first()
    if user and user.used_code == ch:
        raise HTTPException(
            409, "Dieser Einmalcode wurde bereits benutzt. Für ein neues Passwort "
                 "bitte einen neuen Code anfordern.")
    if user:
        user.password_hash = hash_password(pw)
        user.used_code = ch
    else:
        user = User(email=email, password_hash=hash_password(pw), used_code=ch)
        session.add(user)
    session.commit()
    session.refresh(user)
    request.session["uid"] = user.id
    return {"email": user.email, "is_admin": user.is_admin}


@app.post("/api/auth/login")
def login(payload: dict, request: Request, session: Session = Depends(get_session)):
    _rate_limit(request)
    email = (payload.get("email") or "").strip().lower()
    pw = payload.get("password") or ""
    if not _allowed(email):
        raise HTTPException(403, "Diese E-Mail ist nicht freigeschaltet.")
    user = session.exec(select(User).where(User.email == email)).first()
    if not user or not verify_password(pw, user.password_hash):
        raise HTTPException(401, "E-Mail oder Passwort falsch.")
    request.session["uid"] = user.id
    return {"email": user.email, "is_admin": user.is_admin}


@app.post("/api/auth/logout", status_code=204)
def logout(request: Request):
    request.session.clear()


@app.get("/api/auth/me")
def me(user: User = Depends(get_current_user)):
    return {"email": user.email, "is_admin": user.is_admin}


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
async def directions(payload: dict, user: User = Depends(get_current_user)) -> dict:
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
async def places(payload: dict, user: User = Depends(get_current_user)) -> dict:
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
async def geocode(q: str, user: User = Depends(get_current_user)) -> dict:
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
def list_trips(user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    return session.exec(
        select(Trip).where(Trip.user_id == user.id).order_by(Trip.start_datum, Trip.id)
    ).all()


@app.post("/api/trips", response_model=Trip, status_code=201)
def create_trip(
    data: TripCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    trip = Trip.model_validate(data, update={"user_id": user.id})
    session.add(trip)
    session.commit()
    session.refresh(trip)
    return trip


@app.get("/api/trips/{trip_id}", response_model=Trip)
def get_trip(
    trip_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return _owned_trip(session, trip_id, user)


@app.patch("/api/trips/{trip_id}", response_model=Trip)
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


@app.delete("/api/trips/{trip_id}", status_code=204)
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


# ---- Stops -------------------------------------------------------------------
@app.get("/api/trips/{trip_id}/stops", response_model=List[Stop])
def list_stops(
    trip_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _owned_trip(session, trip_id, user)
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
    trip_id: int,
    data: StopCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _owned_trip(session, trip_id, user)
    _validate_status(data.status)
    stop = Stop.model_validate(data, update={"trip_id": trip_id})
    session.add(stop)
    _touch_trip(session, trip_id)
    session.commit()
    session.refresh(stop)
    return stop


@app.put("/api/trips/{trip_id}/stops/order", response_model=List[Stop])
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


@app.patch("/api/stops/{stop_id}", response_model=Stop)
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
    stop.updated_at = datetime.utcnow()
    session.add(stop)
    _touch_trip(session, stop.trip_id)
    session.commit()
    session.refresh(stop)
    return stop


@app.delete("/api/stops/{stop_id}", status_code=204)
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


# ---- Statisches PWA-Frontend (nach den API-Routen gemountet) -----------------
_frontend = Path(__file__).resolve().parent.parent / "frontend"
if _frontend.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend), html=True), name="frontend")
