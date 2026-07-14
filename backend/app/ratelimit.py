"""Ratenbegrenzung (Sliding-Window, in-memory pro Prozess).

WICHTIG (S1): Die echte Client-IP kommt nur korrekt an, wenn uvicorn mit
--proxy-headers + --forwarded-allow-ips läuft (siehe Dockerfile). Hinter dem
Reverse-Proxy (Caddy/Traefik) wäre request.client.host sonst die Proxy-IP ->
ein gemeinsamer Bucket (Schutz wirkungslos + Login-DoS).
(C1: aus main.py extrahiert – Verhalten unverändert.)"""

import os
import time

from fastapi import HTTPException, Request

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


def _proxy_rate_limit(request: Request, user: "User") -> None:  # noqa: F821
    """S3 – bezahlte Google-Proxy-Endpoints: pro Nutzer (Fallback IP) / Minute,
    damit ein Member/gestohlene Session keine unbegrenzten Kosten auslöst."""
    key = f"u:{user.id}" if getattr(user, "id", None) else f"ip:{_client_ip(request)}"
    _hit_limit(_proxy_hits, key, _PROXY_RL_MAX, 60,
               "Zu viele Karten-Anfragen, bitte kurz warten.")
