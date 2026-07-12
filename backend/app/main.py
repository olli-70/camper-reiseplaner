import csv
import hashlib
import io
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
from fastapi.responses import Response
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


def _parse_members_strict() -> dict:
    """Wie _members(), aber wirft bei ungültigem MEMBERS-JSON, statt still eine leere
    Liste zu liefern. Für die DESTRUKTIVE Reconciliation zwingend: bei kaputter Liste
    darf NICHT gelöscht werden (sonst Datenverlust). Ungültige Einträge (leere E-Mail
    oder leerer Code) führen zum Abbruch – die Liste gilt dann als fehlerhaft."""
    raw = json.loads(os.getenv("MEMBERS", "[]"))
    if not isinstance(raw, list):
        raise ValueError("MEMBERS ist keine JSON-Liste")
    result: dict = {}
    for m in raw:
        email = (m.get("email") or "").strip().lower()
        code = (m.get("code") or "").strip()
        if not email or not code:
            raise ValueError("MEMBERS enthält einen Eintrag mit leerer E-Mail oder leerem Code")
        result[email] = code
    return result


def reconcile_members() -> dict:
    """Bringt die DB-Nutzer mit der MEMBERS-Whitelist in Deckung: Nutzer, die weder
    Admin noch (mehr) in der Liste stehen, werden mitsamt ihren Reisen und Stopps
    GELÖSCHT. Passwörter/Reisen der weiterhin gelisteten Nutzer bleiben unberührt.
    Wirft bei kaputter/ungültiger MEMBERS-Liste (löscht dann NICHTS)."""
    allowed = set(_parse_members_strict().keys())
    admin = _admin_email()
    if admin:
        allowed.add(admin)
    deleted: list = []
    with Session(engine) as session:
        for user in session.exec(select(User)).all():
            if user.is_admin or user.email in allowed:
                continue
            for trip in session.exec(select(Trip).where(Trip.user_id == user.id)).all():
                for stop in session.exec(select(Stop).where(Stop.trip_id == trip.id)).all():
                    session.delete(stop)
                session.delete(trip)
            session.delete(user)
            deleted.append(user.email)
        session.commit()
    return {"deleted": sorted(deleted), "kept_allowed": sorted(allowed)}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _seed_admin()
    yield


def _require_session_secret() -> str:
    """S2 – Fail-closed: Ohne ausreichend starkes SESSION_SECRET startet die App
    NICHT. Ein leeres/kurzes Cookie-Signaturgeheimnis erlaubt sonst gefälschte
    Sessions (uid=Admin). Kein unsicherer Default, kein Weiterlaufen."""
    secret = os.getenv("SESSION_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError(
            "SESSION_SECRET fehlt oder ist zu kurz (min. 32 Zeichen). Start "
            "abgebrochen (Sicherheit): ohne starkes Signaturgeheimnis wären "
            "Sessions fälschbar. Bitte Vault-Feld "
            "secret/camper-reiseplaner:session_secret setzen (>= 32 Zeichen)."
        )
    return secret


app = FastAPI(title="Camper-Reiseplaner", version=__version__, lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=_require_session_secret(),
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


# ---- Ratenbegrenzung (Sliding-Window, in-memory pro Prozess) ----------------
# WICHTIG (S1): Die echte Client-IP kommt nur korrekt an, wenn uvicorn mit
# --proxy-headers + --forwarded-allow-ips läuft (siehe Dockerfile). Hinter dem
# Reverse-Proxy (Caddy/Traefik) wäre request.client.host sonst die Proxy-IP ->
# ein gemeinsamer Bucket (Schutz wirkungslos + Login-DoS).
_login_hits: dict = {}
_proxy_hits: dict = {}
_RL_MAX = int(os.getenv("LOGIN_RATELIMIT", "20"))        # Login-Versuche / 5-Min / IP
_PROXY_RL_MAX = int(os.getenv("PROXY_RATELIMIT", "60"))  # bezahlte Maps-Calls / Min / User


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def _hit_limit(store: dict, key: str, max_hits: int, window: int, msg: str) -> None:
    now = time.time()
    hits = [t for t in store.get(key, []) if now - t < window]
    if len(hits) >= max_hits:
        raise HTTPException(429, msg)
    hits.append(now)
    store[key] = hits


def _rate_limit(request: Request) -> None:
    """Login-/Passwort-Endpoints: pro echter Client-IP (S1)."""
    _hit_limit(_login_hits, _client_ip(request), _RL_MAX, 300,
               "Zu viele Versuche, bitte kurz warten.")


def _proxy_rate_limit(request: Request, user: "User") -> None:
    """S3 – bezahlte Google-Proxy-Endpoints: pro Nutzer (Fallback IP) / Minute,
    damit ein Member/gestohlene Session keine unbegrenzten Kosten auslöst."""
    key = f"u:{user.id}" if getattr(user, "id", None) else f"ip:{_client_ip(request)}"
    _hit_limit(_proxy_hits, key, _PROXY_RL_MAX, 60,
               "Zu viele Karten-Anfragen, bitte kurz warten.")


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
async def directions(payload: dict, request: Request,
                     user: User = Depends(get_current_user)) -> dict:
    """Etappen-Routing (eine Etappe: origin, destination, waypoints[])."""
    _proxy_rate_limit(request, user)   # S3: Kosten-/Abuse-Schutz
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
async def places(payload: dict, request: Request,
                 user: User = Depends(get_current_user)) -> dict:
    """Places-Text-Suche; mit `points` (>=2) -> Suche entlang der Route."""
    _proxy_rate_limit(request, user)   # S3: Kosten-/Abuse-Schutz
    if not _GKEY:
        raise HTTPException(503, "Kein Google-Key konfiguriert")
    q = (payload.get("textQuery") or "").strip()
    if not q:
        return {"places": []}
    # S3: maxResultCount hart auf 1..20 deckeln (ungültige Eingaben -> Default 8).
    try:
        max_results = int(payload.get("maxResultCount", 8))
    except (TypeError, ValueError):
        max_results = 8
    max_results = max(1, min(max_results, 20))
    body = {
        "textQuery": q, "languageCode": "de",
        "maxResultCount": max_results,
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


# ---- OSM/Overpass: Stellplätze im Umkreis eines POI --------------------------
# Server-seitiger Proxy (kein CORS im Browser, kontrollierte Overpass-Last +
# Overpass-Etikette: sprechender User-Agent, Timeout, kurzer Cache). Query auf
# tourism=caravan_site im Umkreis; `nwr` erfasst node/way/relation, `out center`
# liefert auch für ways/relations Mittelpunkt-Koordinaten.
_OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
_OVERPASS_UA = (
    f"camper-reiseplaner/{__version__} "
    "(+https://camper.dorf27.com; self-hosted trip planner)"
)
_CAMPSITE_CACHE: dict = {}   # key -> (expires_ts, payload)  – kurzer In-Memory-Cache
_CAMPSITE_TTL = 600          # 10 Minuten

# OSM-Tags, die fürs Detail-Popup relevant sind (Rest wird verworfen -> schlank).
_CAMPSITE_TAGS = (
    "name", "operator", "fee", "sanitary_dump_station", "capacity",
    "website", "contact:website", "phone", "contact:phone", "email",
    "opening_hours", "description", "tourism", "power_supply", "drinking_water",
    "addr:street", "addr:housenumber", "addr:postcode", "addr:city",
)


async def _overpass_query(query: str) -> dict:
    """Fragt Overpass ab – mehrere Instanzen der Reihe nach (die Haupt-Instanz
    liefert oft 429/504), mit Etikette-User-Agent. Wirft HTTP 502 bei Totalausfall."""
    last_status = None
    async with httpx.AsyncClient(
        timeout=30.0,
        headers={"User-Agent": _OVERPASS_UA, "Referer": _GREFERER},
    ) as client:
        for url in _OVERPASS_ENDPOINTS:
            try:
                r = await client.post(url, data={"data": query})
            except Exception:
                last_status = "timeout"
                continue
            if r.status_code == 200:
                return r.json()
            last_status = r.status_code   # 429/504 -> nächste Instanz probieren
    raise HTTPException(502, f"Overpass nicht erreichbar (Status {last_status})")


@app.post("/api/campsites-nearby")
async def campsites_nearby(payload: dict, user: User = Depends(get_current_user)) -> dict:
    """Stellplätze (OSM ``tourism=caravan_site``) im Umkreis eines Punktes.

    Eingabe ``{lat, lng, radius?}`` (radius in Metern, Default 25000, max 50000).
    Rückgabe ``{count, radius, campsites:[{id, lat, lng, name, tags}]}``.
    """
    try:
        lat = float(payload["lat"])
        lng = float(payload["lng"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, "lat/lng fehlen oder sind ungültig")
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise HTTPException(422, "lat/lng außerhalb des gültigen Bereichs")
    radius = max(1000, min(int(payload.get("radius", 25000)), 50000))

    key = f"{round(lat, 3)},{round(lng, 3)},{radius}"
    now = time.time()
    cached = _CAMPSITE_CACHE.get(key)
    if cached and cached[0] > now:
        return cached[1]

    around = f"around:{radius},{lat:.5f},{lng:.5f}"
    query = (
        "[out:json][timeout:25];"
        f'(nwr["tourism"="caravan_site"]({around}););'
        "out center 120;"
    )
    data = await _overpass_query(query)

    sites = []
    for e in data.get("elements", []):
        center = e.get("center") or {}
        elat = e.get("lat", center.get("lat"))
        elng = e.get("lon", center.get("lon"))
        if elat is None or elng is None:
            continue
        tags = e.get("tags") or {}
        picked = {k: tags[k] for k in _CAMPSITE_TAGS if k in tags}
        sites.append({
            "id": f'{e.get("type", "node")}/{e.get("id")}',
            "lat": elat,
            "lng": elng,
            "name": tags.get("name") or "Wohnmobil-Stellplatz",
            "tags": picked,
        })

    result = {"count": len(sites), "radius": radius, "campsites": sites}
    _CAMPSITE_CACHE[key] = (now + _CAMPSITE_TTL, result)
    if len(_CAMPSITE_CACHE) > 200:  # Cache klein halten: abgelaufene Einträge kappen
        for k in [k for k, v in _CAMPSITE_CACHE.items() if v[0] <= now][:100]:
            _CAMPSITE_CACHE.pop(k, None)
    return result


@app.get("/api/geocode")
async def geocode(q: str, request: Request,
                  user: User = Depends(get_current_user)) -> dict:
    """Adresse -> Koordinaten (für Ortssuche + Tour-Start/Ziel)."""
    _proxy_rate_limit(request, user)   # S3: Kosten-/Abuse-Schutz
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


def _stops_csv(session: Session, trips: List[Trip]) -> str:
    """CSV (Semikolon + UTF-8-BOM, Excel/Numbers-tauglich) der Übernachtungsplätze
    UND POIs der übergebenen Reisen."""
    art = {"stop": "Übernachtung", "poi": "POI"}
    buf = io.StringIO()
    buf.write("﻿")  # UTF-8-BOM
    writer = csv.writer(buf, delimiter=";")
    writer.writerow([
        "Reise", "Art", "Name", "Status", "Notiz",
        "Breitengrad", "Längengrad", "In Route", "Reihenfolge",
        "An (Ankunft)", "Ab (Abfahrt)", "Reserviert",
    ])
    for trip in trips:
        stops = session.exec(
            select(Stop).where(Stop.trip_id == trip.id).order_by(Stop.reihenfolge, Stop.id)
        ).all()
        for s in stops:
            writer.writerow([
                trip.name,
                art.get(s.kind, s.kind),
                s.name,
                s.status,
                s.notiz or "",
                s.lat,
                s.lng,
                "ja" if s.in_route else "nein",
                s.reihenfolge,
                s.reserviert_von.isoformat(sep=" ", timespec="minutes") if s.reserviert_von else "",
                s.reserviert_bis.isoformat(sep=" ", timespec="minutes") if s.reserviert_bis else "",
                "ja" if s.reserviert else "nein",
            ])
    return buf.getvalue()


def _csv_download(content: str, filename: str) -> Response:
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _csv_slug(name: str) -> str:
    return (re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower() or "reise")[:60]


@app.get("/api/export.csv")
def export_csv(user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    """Alle eigenen Reisen als CSV (user-scoped)."""
    trips = session.exec(
        select(Trip).where(Trip.user_id == user.id).order_by(Trip.start_datum, Trip.id)
    ).all()
    return _csv_download(_stops_csv(session, trips), "camper-reisen.csv")


@app.get("/api/trips/{trip_id}/export.csv")
def export_trip_csv(
    trip_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Eine einzelne eigene Reise als CSV (Fremdzugriff -> 404)."""
    trip = _owned_trip(session, trip_id, user)
    return _csv_download(_stops_csv(session, [trip]), f"camper-{_csv_slug(trip.name)}.csv")


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


# ---- CLI: Nutzerliste synchronisieren ----------------------------------------
# Vom user-sync-Playbook per `docker exec ... python -m app.main reconcile-members`
# aufgerufen, NACHDEM der Container mit frischer MEMBERS-ENV neu gestartet wurde.
# Gibt einen JSON-Report (deleted/kept_allowed) aus; Exit-Code != 0 bei Fehler.
if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "reconcile-members":
        try:
            print(json.dumps(reconcile_members(), ensure_ascii=False))
        except Exception as exc:  # keine DB-Änderung passiert -> nur melden
            print(f"FEHLER reconcile-members: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        print("Nutzung: python -m app.main reconcile-members", file=sys.stderr)
        sys.exit(2)
