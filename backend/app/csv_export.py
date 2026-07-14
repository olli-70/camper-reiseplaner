"""CSV-Export der Reisen (Übernachtungsplätze + POIs). (C1: aus main.py
extrahiert – Verhalten unverändert.)"""

import csv
import io
import re
from typing import List

from fastapi.responses import Response
from sqlmodel import Session, select

from .models import Stop, Trip


def _stops_csv(session: Session, trips: List[Trip]) -> str:
    """CSV (Semikolon + UTF-8-BOM, Excel/Numbers-tauglich) der Übernachtungsplätze
    UND POIs der übergebenen Reisen."""
    art = {"stop": "Übernachtung", "poi": "POI"}
    buf = io.StringIO()
    buf.write("﻿")  # UTF-8-BOM
    writer = csv.writer(buf, delimiter=";")
    writer.writerow([
        "Reise", "Art", "Name", "Status", "Notiz",
        "Breitengrad", "Längengrad", "In Route", "Reihenfolge",
        "An (Ankunft)", "Ab (Abfahrt)", "Reserviert",
    ])
    for trip in trips:
        stops = session.exec(
            select(Stop).where(Stop.trip_id == trip.id).order_by(Stop.reihenfolge, Stop.id)
        ).all()
        for s in stops:
            writer.writerow([
                trip.name,
                art.get(s.kind, s.kind),
                s.name,
                s.status,
                s.notiz or "",
                s.lat,
                s.lng,
                "ja" if s.in_route else "nein",
                s.reihenfolge,
                s.reserviert_von.isoformat(sep=" ", timespec="minutes") if s.reserviert_von else "",
                s.reserviert_bis.isoformat(sep=" ", timespec="minutes") if s.reserviert_bis else "",
                "ja" if s.reserviert else "nein",
            ])
    return buf.getvalue()


def _csv_download(content: str, filename: str) -> Response:
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _csv_slug(name: str) -> str:
    return (re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower() or "reise")[:60]
