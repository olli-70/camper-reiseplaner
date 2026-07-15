"""Admin-Auswertung: Nutzung + geschätzte API-Kosten pro Nutzer/Monat.
Nur für Admin-Konten (403 sonst). (Usage-Domäne, Variante A)"""

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from ..csv_export import _csv_download
from ..db import get_session
from ..deps import get_current_user
from ..models import User
from .. import usage

router = APIRouter()


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Nur für Administratoren.")
    return user


@router.get("/api/admin/usage")
def admin_usage(admin: User = Depends(require_admin),
                session: Session = Depends(get_session)) -> dict:
    """Aggregat pro Nutzer/Monat/Metrik + Kostenschätzung (nur aggregierte Zähler)."""
    return usage.summary(session)


@router.get("/api/admin/usage.csv")
def admin_usage_csv(admin: User = Depends(require_admin),
                    session: Session = Depends(get_session)):
    return _csv_download(usage.summary_csv(session), "camper-usage.csv")
