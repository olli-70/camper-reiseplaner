"""Auth-/Sicherheits-Primitive: Whitelist, Passwörter, Admin-Seed, Member-
Reconciliation, SESSION_SECRET-Guard. (C1: aus dem früheren monolithischen
main.py extrahiert – Verhalten unverändert.)"""

import hashlib
import json
import os
import re

import bcrypt
from sqlmodel import Session, select

from .db import engine
from .models import Stop, Trip, User

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


# S7: Dummy-Hash, damit der Login auch bei unbekannter/nicht-freigeschalteter
# E-Mail EINE echte bcrypt-Prüfung durchläuft -> konstante Zeit, kein Timing-/
# Status-Orakel für Account-Enumeration.
_DUMMY_HASH = bcrypt.hashpw(b"invalid-user-timing-equalizer", bcrypt.gensalt()).decode("utf-8")


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


def require_session_secret() -> str:
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
