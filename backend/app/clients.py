"""Externe HTTP-Clients: Google-Web-Dienste (server-seitig, Key erreicht den
Browser NIE) und OSM/Overpass. (C1: aus main.py extrahiert – Verhalten
unverändert. C3-Pooling folgt separat.)

Server-Key: bevorzugt GOOGLE_MAPS_SERVER_KEY (IP-beschränkt, keine Referrer-
Beschränkung); Fallback = Render-Key. Wir senden zusätzlich einen Referer-
Header, damit auch ein referrer-beschränkter Key server-seitig funktioniert
(Übergangszustand, bis ein dedizierter Server-Key existiert)."""

import os

import httpx
from fastapi import HTTPException

from . import __version__

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


# ---- OSM/Overpass ------------------------------------------------------------
_OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
_OVERPASS_UA = (
    f"camper-reiseplaner/{__version__} "
    "(+https://camper.dorf27.com; self-hosted trip planner)"
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
