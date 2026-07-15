"""Google-Web-Dienste server-seitig (Directions/Places/Geocoding). Der Google-Key
erreicht den Browser NIE; Proxy-Endpoints sind rate-limited (S3). (C1)"""

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import usage
from ..clients import _GKEY, _GREFERER, _encode_polyline, _gget, _gpost
from ..deps import get_current_user
from ..models import DirectionsRequest, User
from ..ratelimit import _proxy_rate_limit

router = APIRouter()


@router.post("/api/directions")
async def directions(req: DirectionsRequest, request: Request,
                     user: User = Depends(get_current_user)) -> dict:
    """Etappen-Routing (eine Etappe: origin, destination, waypoints[]). C2: origin/
    destination/waypoints werden per Pydantic validiert (fehlend/ungültig -> 422)."""
    _proxy_rate_limit(request, user)   # S3: Kosten-/Abuse-Schutz
    if not _GKEY:
        raise HTTPException(503, "Kein Google-Key konfiguriert")
    usage.bump(user.id, "api_directions")
    params = {
        "origin": f"{req.origin.lat},{req.origin.lng}",
        "destination": f"{req.destination.lat},{req.destination.lng}",
        "mode": "driving", "language": "de",
    }
    if req.waypoints:
        params["waypoints"] = "|".join(f"{w.lat},{w.lng}" for w in req.waypoints)
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
    usage.bump(user.id, "api_places")
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
    data = await _gpost("https://places.googleapis.com/v1/places:searchText", body, headers)
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
    usage.bump(user.id, "api_geocode")
    data = await _gget(
        "https://maps.googleapis.com/maps/api/geocode/json", {"address": q, "language": "de"})
    out = []
    for res in data.get("results", []):
        loc = res["geometry"]["location"]
        addr = res.get("formatted_address", "")
        out.append({"name": addr.split(",")[0], "display": addr, "lat": loc["lat"], "lng": loc["lng"]})
    return {"results": out}
