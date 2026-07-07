# 🚐 Camper-Reiseplaner

Self-hosted **PWA** zum Planen von Camper-Reisen: Touren mit Übernachtungsplätzen
und POIs, Kartenansicht, Straßen-Entfernungen + Fahrzeiten, Reservierungen.
Single-User (Olli & Anja), Zugriff über **Tailscale** – kein Login im Backend.

Live: **https://camper.dorf27.com** (nur intern / Tailnet)

## Funktionen

**Touren**
- Mehrere Reisen; Umschalten über das Tour-Menü (Tour-Name ▾), Standard = zuletzt beplante.
- Tour-Einstellungen (⚙️): **Start-/Zieladresse** (leer = Rundreise) und **Abfahrt/Rückkehr**.
  Adressen tauchen nicht in der Liste auf, werden aber in die Entfernungen einbezogen.

**Übernachtungsplätze** (Liste, sortierbar per Drag)
- Name, Status (geplant/besucht/reserviert), Notiz, Datum.
- **Reservierung**: Häkchen + „reserviert von/bis"; Anzeige `🚐✅` (reserviert) / `🚐❓` (offen),
  Zeile `An: TT.MM HH:MM · ab: TT.MM HH:MM`.
- **Straßen-km + Fahrzeit** je Etappe (eingehend, inkl. Abfahrtsort → 1. Ort) und gesamt.
- **⚠️ Reservierungs-Warnung**, wenn Ende(Vorplatz)+Fahrzeit > Start(nächster Platz).
- Marker farblich nach Status, per Finger/Maus verschiebbar; Popup mit
  „In Apple/Google Maps öffnen" (Deep-Links, kein Key).

**POIs** (nur Punkte, keine Übernachtung)
- Aufklappmenü „📍 Punkte" (standardmäßig zu); Notizen pro POI.
- Klick auf einen POI → **Entfernungen zu allen Übernachtungsplätzen** (sortiert).

**Karte**: Google Maps (Karte/Satellit/Gelände), „Mein Standort".
**PWA**: installierbar (iPhone/Mac), Offline-Read-Cache, nahtloses Auto-Update.

## Architektur

```
Browser (PWA)  ──REST──►  FastAPI  ──►  SQLite (/data/camper.db)
   Google Maps                app/main.py, models.py, db.py
```

- **Ein Container**: FastAPI serviert die REST-API *und* die statische PWA.
- **Datenmodell:** `Trip` 1─n `Stop`. `Stop.kind` = `stop` (Übernachtung, in Liste/Route)
  oder `poi` (nur Punkt).

**Externe Dienste** (alle für 2 Nutzer gratis):
| Zweck | Dienst | Key? |
|---|---|---|
| Basiskarte | Google Maps JS (Dynamic Maps, 10k/Mon frei) | Key (Vault) |
| Routing (km/Zeit, Matrix) | OSRM Demo (`router.project-osrm.org`) | nein |
| Adress-/Reverse-Geocoding | OpenStreetMap Nominatim | nein |

Der Google-Maps-Key liegt in Vault (`secret/camper-reiseplaner` → `api_key`,
per Referrer auf `camper.dorf27.com` beschränkt), wird beim Deploy als ENV gesetzt
und über `GET /api/config` clientseitig ausgeliefert.

## API (Kurzüberblick)

| Methode | Pfad | Zweck |
|---|---|---|
| GET | `/api/health`, `/api/config` | Health / Google-Maps-Key |
| GET/POST | `/api/trips` | Reisen listen / anlegen |
| GET/PATCH/DELETE | `/api/trips/{id}` | Reise lesen / ändern / löschen |
| GET/POST | `/api/trips/{id}/stops` | Stopps + POIs einer Reise |
| PUT | `/api/trips/{id}/stops/order` | Reihenfolge speichern |
| PATCH/DELETE | `/api/stops/{id}` | Stopp/POI ändern / löschen |

## Lokal starten

```bash
docker compose up --build      # -> http://localhost:8082
# Google-Maps-Key: ENV GOOGLE_MAPS_API_KEY=... setzen (sonst lädt die Karte nicht)
```

## Tests

```bash
cd backend && pip install -r requirements.txt pytest httpx && PYTHONPATH=. pytest -q
```

## Betrieb (Homelab)

- **Deploy:** Ansible `ansible/playbooks/docker/camper-reiseplaner.yml` auf **dockermsa2**
  (Build-from-source aus Gitea, hinter Traefik/TLS, `camper.dorf27.com` via UniFi-CNAME).
- **Erreichbarkeit:** intern/Tailnet (kein öffentlicher Port, kein Login – Tailscale).
- **Backup:** Semaphore Backup v2 (`semaphore-homelab`, Typ `camper`): täglicher
  konsistenter SQLite-Online-Snapshot nach NFS + Kopia-Versionierung.
- **Schema-Änderungen:** additive Auto-Migration beim Start (siehe `docs/ARCHITEKTUR.md`).

## Nicht-Ziele
- Keine kostenpflichtigen Dienste über die Gratis-Kontingente hinaus, kein Multi-Tenant,
  kein eigenes Login (Tailscale). Offline-**Bearbeiten** und Offline-**Karte** sind nicht dabei.
