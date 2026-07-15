"""App-Zusammenbau: Middleware (Session + Security-Header/CSP), Lifespan,
Router-Wiring und statisches PWA-Frontend.

C1: Die Fachlogik liegt in Domänen-Modulen –
  security.py / ratelimit.py / deps.py / clients.py / csv_export.py
  routers/{health,auth,maps,campsites,trips,stops}.py
Dieses main.py verdrahtet sie nur noch (Router→Service→Repository light,
FastAPI-Depends()-DI). Verhalten unverändert.
"""

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import __version__
from .db import init_db
from .routers import admin, auth, campsites, health, maps, stops, trips
from .security import _seed_admin, reconcile_members, require_session_secret


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _seed_admin()
    yield
    # C3: gemeinsamen httpx-Client sauber schließen.
    from .clients import aclose_client
    await aclose_client()


app = FastAPI(title="Camper-Reiseplaner", version=__version__, lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=require_session_secret(),
    same_site="lax",
    https_only=os.getenv("COOKIE_SECURE", "1") != "0",
    max_age=60 * 60 * 24 * 7,  # S6: 7 Tage (vorher 30) – kürzeres Zeitfenster
)


# ---- S4: Security-Header + Content-Security-Policy ---------------------------
# CSP ist bewusst so gebaut, dass Google Maps JS + PWA weiter funktionieren:
#  - script-src erlaubt Google-Maps-Loader (+ 'unsafe-eval' für Maps-Vector/WebGL),
#    KEIN 'unsafe-inline' (index.html lädt Scripts nur via src, kein Inline-JS).
#  - style-src 'unsafe-inline': Google Maps injiziert massenhaft Inline-Styles.
#  - connect-src listet ALLE Browser-Direktziele (Google + OSM/OSRM/Overpass/
#    Nominatim). Enger möglich, sobald diese Calls serverseitig proxied sind (C4).
#  - img-src erlaubt Google-Tiles (data:/blob: für Marker/Canvas).
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-eval' https://maps.googleapis.com https://maps.gstatic.com; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' data: https://fonts.gstatic.com; "
    "img-src 'self' data: blob: https://maps.googleapis.com https://maps.gstatic.com "
    "https://*.googleapis.com https://*.gstatic.com https://*.google.com https://*.ggpht.com; "
    "connect-src 'self' https://maps.googleapis.com https://maps.gstatic.com https://*.googleapis.com "
    "https://nominatim.openstreetmap.org https://router.project-osrm.org "
    "https://overpass-api.de https://overpass.kumi.systems https://overpass.private.coffee; "
    "worker-src 'self' blob:; "
    "frame-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)
_SECURITY_HEADERS = {
    "Content-Security-Policy": _CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(self), camera=(), microphone=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    for k, v in _SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    return response


# ---- Router je Domäne (C1) ---------------------------------------------------
for _module in (health, auth, maps, campsites, trips, stops, admin):
    app.include_router(_module.router)


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
