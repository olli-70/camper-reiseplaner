"""Nutzungs-/Kosten-Zählung (Variante A).

DSGVO-arm: es werden AUSSCHLIESSLICH aggregierte Monats-Zähler pro
(Nutzer, Monat, Metrik) gespeichert – KEINE Einzel-Events, keine Zeitstempel je
Aktion, keine Koordinaten, keine IPs, keine Request-Bodies.

Increment via UPSERT (SQLite ON CONFLICT). Best-effort: die Zählung darf einen
Request NIE bremsen oder scheitern lassen -> Fehler werden geschluckt.
"""

from datetime import date, datetime, timezone

from sqlalchemy import text
from sqlmodel import Session

from .db import engine
from .models import User

# ---- kostenrelevante API-Metriken --------------------------------------------
# Roh-Zähler; die Kostenschätzung nutzt die SKU-Preise unten.
API_METRICS = ("api_directions", "api_places", "api_geocode")

# Maps-Platform-SKU-Preise (EUR pro 1.000 Calls über dem Free-Tier).
# WICHTIG: KONSISTENT halten mit der EINEN Preisquelle im GCP-Maps-Monitor
#   semaphore-homelab: python/gcp-maps-monitor/gcp_maps_usage.py  SKU_PRICE_PER_1000
# (dort USD, hier 1:1 als EUR wie im Kill-Switch). Bei Preisänderung BEIDE anpassen.
_SKU_EUR_PER_1000 = {
    "api_directions": 5.0,
    "api_geocode": 5.0,
    "api_places": 35.0,   # Places (New) Text Search – teuerste SKU, konservativ
}
FREE_TIER_PER_MONTH = 10000  # Calls/API/Monat gratis (Maps Platform Essentials)


def _period_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def bump(user_id, metric: str, n: int = 1) -> None:
    """Erhöht einen Monatszähler (UPSERT). Best-effort – schluckt jeden Fehler."""
    if not user_id:
        return
    period = _period_now()
    try:
        with Session(engine) as s:
            s.exec(
                text(
                    "INSERT INTO usagecounter (user_id, period, metric, count) "
                    "VALUES (:u, :p, :m, :n) "
                    "ON CONFLICT(user_id, period, metric) "
                    "DO UPDATE SET count = count + :n"
                ),
                params={"u": user_id, "p": period, "m": metric, "n": n},
            )
            s.commit()
    except Exception:
        pass  # Zählung darf den Request niemals stören


def touch_active_day(session: Session, user: User) -> None:
    """Zählt einen aktiven Tag/Monat – höchstens 1× pro UTC-Tag und Nutzer
    (wird in get_current_user aufgerufen). Best-effort."""
    today = datetime.now(timezone.utc).date()
    try:
        if user.last_seen == today:
            return
        user.last_seen = today
        session.add(user)
        session.commit()
        bump(user.id, "active_day")
    except Exception:
        pass


def summary(session: Session) -> dict:
    """Aggregat pro Nutzer/Monat/Metrik + Kostenschätzung der API-Calls.
    Rückgabe: {rows:[{email, period, metric, count}], cost_estimate_eur:{email: eur}}.
    """
    sql = text(
        "SELECT u.email AS email, c.period AS period, c.metric AS metric, c.count AS count "
        "FROM usagecounter c JOIN user u ON u.id = c.user_id "
        "ORDER BY u.email, c.period DESC, c.metric"
    )
    rows = [dict(r._mapping) for r in session.exec(sql)]
    cost: dict = {}
    for r in rows:
        price = _SKU_EUR_PER_1000.get(r["metric"])
        if price is None:
            continue
        billable = max(0, r["count"] - FREE_TIER_PER_MONTH)
        cost[r["email"]] = round(cost.get(r["email"], 0.0) + billable / 1000.0 * price, 2)
    return {"rows": rows, "cost_estimate_eur": cost,
            "note": "Kosten = geschätzt aus Calls über Free-Tier x SKU-Preis (EUR); "
                    "Map-Loads sind browserseitig und NICHT pro Nutzer erfassbar "
                    "(nur im GCP-Maps-Monitor als Gesamtwert)."}


def summary_csv(session: Session) -> str:
    import csv
    import io
    data = summary(session)
    buf = io.StringIO()
    buf.write("﻿")  # UTF-8-BOM
    w = csv.writer(buf, delimiter=";")
    w.writerow(["E-Mail", "Monat", "Metrik", "Anzahl"])
    for r in data["rows"]:
        w.writerow([r["email"], r["period"], r["metric"], r["count"]])
    w.writerow([])
    w.writerow(["E-Mail", "geschätzte API-Kosten (EUR, laufende Summe)"])
    for email, eur in sorted(data["cost_estimate_eur"].items()):
        w.writerow([email, f"{eur:.2f}"])
    return buf.getvalue()
