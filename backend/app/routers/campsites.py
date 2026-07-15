"""OSM/Overpass: Stellplätze im Umkreis eines POI. Server-seitiger Proxy (kein
CORS im Browser, kontrollierte Overpass-Last + Etikette, kurzer Cache). (C1)"""

import time

from fastapi import APIRouter, Depends

from .. import usage
from ..clients import _overpass_query
from ..deps import get_current_user
from ..models import CampsitesRequest, User

router = APIRouter()

_CAMPSITE_CACHE: dict = {}   # key -> (expires_ts, payload)  – kurzer In-Memory-Cache
_CAMPSITE_TTL = 600          # 10 Minuten

# OSM-Tags, die fürs Detail-Popup relevant sind (Rest wird verworfen -> schlank).
_CAMPSITE_TAGS = (
    "name", "operator", "fee", "sanitary_dump_station", "capacity",
    "website", "contact:website", "phone", "contact:phone", "email",
    "opening_hours", "description", "tourism", "power_supply", "drinking_water",
    "addr:street", "addr:housenumber", "addr:postcode", "addr:city",
)


@router.post("/api/campsites-nearby")
async def campsites_nearby(req: CampsitesRequest,
                           user: User = Depends(get_current_user)) -> dict:
    """Stellplätze (OSM ``tourism=caravan_site``) im Umkreis eines Punktes.

    Eingabe ``{lat, lng, radius?}`` (radius in Metern, Default 25000, max 50000).
    C2: lat/lng/radius werden per Pydantic validiert (fehlend/ungültig/Bereich -> 422).
    Rückgabe ``{count, radius, campsites:[{id, lat, lng, name, tags}]}``.
    """
    lat, lng = req.lat, req.lng
    usage.bump(user.id, "campsites")  # gratis (Overpass), aber fürs Aktivitätsbild
    radius = max(1000, min(req.radius, 50000))

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
