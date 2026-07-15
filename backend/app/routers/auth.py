"""Authentifizierung (E-Mail + Passwort, Session-Cookie). (C1)"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select

from ..db import get_session
from ..deps import get_current_user
from ..models import User
from ..ratelimit import _rate_limit
from ..security import (
    _DUMMY_HASH,
    _code_hash,
    _allowed,
    _members,
    hash_password,
    verify_password,
)

router = APIRouter()


@router.post("/api/auth/set-password")
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
        user.token_version = (user.token_version or 0) + 1  # S6: alte Sessions raus
    else:
        user = User(email=email, password_hash=hash_password(pw), used_code=ch)
        session.add(user)
    session.commit()
    session.refresh(user)
    request.session["uid"] = user.id
    request.session["tv"] = user.token_version or 0  # S6
    return {"email": user.email, "is_admin": user.is_admin}


@router.post("/api/auth/login")
def login(payload: dict, request: Request, session: Session = Depends(get_session)):
    _rate_limit(request)
    email = (payload.get("email") or "").strip().lower()
    pw = payload.get("password") or ""
    # S7: keine Account-Enumeration – ob E-Mail nicht freigeschaltet, Nutzer
    # unbekannt oder Passwort falsch: IMMER dieselbe 401-Antwort UND genau eine
    # bcrypt-Prüfung (gegen Dummy-Hash, wenn kein Nutzer) für konstante Zeit.
    user = None
    if _allowed(email):
        user = session.exec(select(User).where(User.email == email)).first()
    ok = verify_password(pw, user.password_hash) if user else verify_password(pw, _DUMMY_HASH)
    if not user or not ok:
        raise HTTPException(401, "E-Mail oder Passwort falsch.")
    request.session["uid"] = user.id
    request.session["tv"] = user.token_version or 0  # S6
    from .. import usage
    usage.bump(user.id, "login")
    return {"email": user.email, "is_admin": user.is_admin}


@router.post("/api/auth/logout", status_code=204)
def logout(request: Request):
    request.session.clear()


@router.post("/api/auth/logout-all", status_code=204)
def logout_all(request: Request, session: Session = Depends(get_session),
               user: User = Depends(get_current_user)):
    """S6: 'überall abmelden' – token_version erhöhen -> ALLE bestehenden Sessions
    (inkl. dieser) werden ungültig. Widerruf eines evtl. gestohlenen Cookies."""
    user.token_version = (user.token_version or 0) + 1
    session.add(user)
    session.commit()
    request.session.clear()


@router.get("/api/auth/me")
def me(user: User = Depends(get_current_user)):
    return {"email": user.email, "is_admin": user.is_admin}
