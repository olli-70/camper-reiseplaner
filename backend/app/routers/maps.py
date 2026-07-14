"""Google-Web-Dienste server-seitig (Directions/Places/Geocoding). Der Google-Key
erreicht den Browser NIE; Proxy-Endpoints sind rate-limited (S3). (C1)"""

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from ..clients import _GKEY, _GREFERER, _encode_polyline, _gget
from ..deps import get_current_user
from ..models import User
from ..ratelimit import _proxy_rate_limit

router = APIRouter()


@router.post("/api/directions")
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


@router.post("/api/places")
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


@router.get("/api/geocode")
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
