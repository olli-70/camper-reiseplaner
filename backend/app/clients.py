"""Externe HTTP-Clients: Google-Web-Dienste (server-seitig, Key erreicht den
Browser NIE) und OSM/Overpass. (C1: aus main.py extrahiert – Verhalten
unverändert. C3-Pooling folgt separat.)

Server-Key: bevorzugt GOOGLE_MAPS_SERVER_KEY (IP-beschränkt, keine Referrer-
Beschränkung); Fallback = Render-Key. Wir senden zusätzlich einen Referer-
Header, damit auch ein referrer-beschränkter Key server-seitig funktioniert
(Übergangszustand, bis ein dedizierter Server-Key existiert)."""

import os
from typing import Optional

import httpx
from fastapi import HTTPException

from . import __version__

_GKEY = os.getenv("GOOGLE_MAPS_SERVER_KEY") or os.getenv("GOOGLE_MAPS_API_KEY", "")
_GREFERER = os.getenv("GOOGLE_KEY_REFERER", "https://camper.dorf27.com/")


# ---- C3: EIN gemeinsamer AsyncClient (Connection-Pooling/Keep-Alive) ----------
# Statt pro Request einen neuen httpx.AsyncClient zu erzeugen, teilen sich alle
# ausgehenden Aufrufe einen Client. Timeout/Headers werden PRO Aufruf gesetzt.
# main.py schließt ihn im Lifespan-Shutdown (aclose_client()).
_shared_client: Optional[httpx.AsyncClient] = None


def _client() -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient()
    return _shared_client


async def aclose_client() -> None:
    global _shared_client
    if _shared_client is not None and not _shared_client.is_closed:
        await _shared_client.aclose()
    _shared_client = None


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
    r = await _client().get(url, params=params, headers={"Referer": _GREFERER}, timeout=20.0)
    return r.json()


async def _gpost(url: str, json_body: dict, headers: dict) -> dict:
    """POST an einen Google-Dienst über den gemeinsamen Client (C3)."""
    r = await _client().post(url, json=json_body, headers=headers, timeout=20.0)
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
    headers = {"User-Agent": _OVERPASS_UA, "Referer": _GREFERER}
    for url in _OVERPASS_ENDPOINTS:
        try:
            r = await _client().post(url, data={"data": query}, headers=headers, timeout=30.0)
        except Exception:
            last_status = "timeout"
            continue
        if r.status_code == 200:
            return r.json()
        last_status = r.status_code   # 429/504 -> nächste Instanz probieren
    raise HTTPException(502, f"Overpass nicht erreichbar (Status {last_status})")
