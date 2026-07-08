# 🚐 Camper-Reiseplaner

Ein **selbst gehosteter Reiseplaner** für Wohnmobil-/Camper-Touren, als
installierbare **PWA** (Progressive Web App). Plane mehrere Reisen mit
Übernachtungsplätzen und Points of Interest, sieh die **gefahrene Route je Etappe**
auf der Karte, bekomme **Straßen-Entfernungen + Fahrzeiten** und verwalte
**Reservierungen** – alles auf deiner eigenen Infrastruktur.

- **Ein einziger Container** (FastAPI + statische PWA), Daten in **SQLite**.
- **Deploy in wenigen Minuten** per Docker Compose.
- Nur externe Abhängigkeit: ein **Google-Maps-API-Key** (für Karte + Routing;
  großzügiges Gratis-Kontingent, siehe [Kosten](#kosten)).

> **App-Sprache:** Die Oberfläche ist auf **Deutsch**.

---

## Inhalt

- [Funktionen](#funktionen)
- [Technik & Architektur](#technik--architektur)
- [Schnellstart (Docker Compose)](#schnellstart-docker-compose)
  - [1. Voraussetzungen](#1-voraussetzungen)
  - [2. Google-Maps-API-Key besorgen](#2-google-maps-api-key-besorgen)
  - [3. Projekt starten](#3-projekt-starten)
- [⚠️ Sicherheit: kein eingebautes Login](#️-sicherheit-kein-eingebautes-login)
- [HTTPS / Reverse-Proxy (empfohlen)](#https--reverse-proxy-empfohlen)
- [Konfiguration (Umgebungsvariablen)](#konfiguration-umgebungsvariablen)
- [Daten & Backup](#daten--backup)
- [Updates](#updates)
- [Kosten](#kosten)
- [Entwicklung & Tests](#entwicklung--tests)
- [API-Überblick](#api-überblick)
- [Datenmodell & Erweiterbarkeit](#datenmodell--erweiterbarkeit)
- [Einschränkungen / Nicht-Ziele](#einschränkungen--nicht-ziele)
- [Lizenz](#lizenz)

---

## Funktionen

**Touren**
- Beliebig viele Reisen; Umschalten über das Tour-Menü (Tour-Name ▾),
  Standard = zuletzt bearbeitete Reise.
- Tour-Einstellungen (⚙️): **Start-/Zieladresse** (leer lassen = Rundreise) und
  **Abfahrt/Rückkehr**. Start/Ziel erscheinen nicht in der Liste, fließen aber in
  die Route und die Entfernungen ein.
- Reise **absperren** (Checkbox): schützt vor versehentlichem Ändern, jederzeit
  wieder lösbar.

**Übernachtungsplätze** (Liste, per Drag & Drop sortierbar)
- Name, Status (geplant/besucht/reserviert), Notiz, Datum.
- **Reservierung**: Häkchen + „reserviert von/bis"; Anzeige `🚐✅` (reserviert) /
  `🚐❓` (offen) und Zeile `An: TT.MM HH:MM · ab: TT.MM HH:MM`.
- **Route auf der Karte**: die Strecke wird gezeichnet – **je Etappe zwischen zwei
  Übernachtungen eine eigene, abwechselnd gefärbte Linie**.
- **Straßen-km + Fahrzeit**: Übernachtung zeigt die Etappen-Summe (seit dem letzten
  Halt), ein Wegpunkt seinen einzelnen Umweg-Sprung; unten die Gesamtstrecke.
- **⚠️ Reservierungs-Warnung**, wenn Abfahrt vom Vorplatz + Fahrzeit die
  Reservierung des nächsten Platzes überschneidet.
- Marker farblich nach Status, per Finger/Maus verschiebbar; Popup mit
  „In Apple/Google Maps öffnen" (Deep-Links, kein Key nötig).

**POIs** (Points of Interest, ohne Übernachtung)
- Aufklappmenü „📍 Punkte"; Notiz je POI.
- Klick auf einen POI → **Entfernungen zu allen Übernachtungsplätzen** (sortiert).
- **„in Route"**-Schalter je POI: nimmt den Punkt als **Wegpunkt** in die Route auf
  (erscheint sortierbar in der Liste 🚏, die Linie führt hindurch). Ohne den
  Schalter bleibt er nur ein Punkt auf der Karte.

**Karte & PWA**
- Google Maps (Karte/Satellit/Gelände), „Mein Standort"-Button.
- **Installierbar** (iPhone „Zum Home-Bildschirm", Desktop-Chrome), Offline-Lese-
  Cache und nahtloses Auto-Update nach jedem Deploy.

---

## Technik & Architektur

```
Browser (PWA, Google Maps)  ──REST──►  FastAPI  ──►  SQLite (/data/camper.db)
   frontend/ (Vanilla JS)              backend/app/main.py, models.py, db.py
```

- **Ein Container**: FastAPI liefert die REST-API **und** die statische PWA aus
  (kein separater Webserver, kein Node-Build – reines HTML/CSS/JS).
- **Backend:** Python 3.12, FastAPI, SQLModel, SQLite. Beim Start legt eine
  additive **Auto-Migration** fehlende Spalten selbst an – Updates brauchen kein
  manuelles Schema-Handling.
- **Frontend:** Vanilla JavaScript + [SortableJS](https://sortablejs.github.io/Sortable/)
  (per CDN). Kein Framework, kein Bundler.

**Externe Dienste**

| Zweck | Dienst | Key nötig? |
|---|---|---|
| Basiskarte | Google Maps JavaScript API | **ja** |
| Route + Etappen-km/Fahrzeit | Google Directions API | **ja** |
| Ortssuche | Google Places API (New) → Geocoding → OSM Nominatim (Fallback) | ja / nein |
| POI→Plätze-Entfernungsmatrix (im POI-Popup) | [OSRM](https://project-osrm.org/) Demo-Server | nein |

Der Google-Key wird als Umgebungsvariable gesetzt und über `GET /api/config`
an den Browser ausgeliefert (er ist dort zwangsläufig sichtbar – deshalb die
[Referrer-Beschränkung](#2-google-maps-api-key-besorgen)).

---

## Schnellstart (Docker Compose)

### 1. Voraussetzungen

- **Docker** + **Docker Compose** (`docker compose version` ≥ v2).
- Ein **Google-Cloud-Konto** für den Maps-Key (Schritt 2).
- Optional, aber empfohlen: eine **Domain** und ein Reverse-Proxy mit HTTPS
  (für PWA-Installation und die Referrer-Beschränkung des Keys).

### 2. Google-Maps-API-Key besorgen

1. In der [Google Cloud Console](https://console.cloud.google.com/) ein **Projekt**
   anlegen und ein **Rechnungskonto** verknüpfen (für die Gratis-Kontingente
   erforderlich; im normalen Privat-Betrieb entstehen praktisch keine Kosten –
   siehe [Kosten](#kosten)).
2. Unter **APIs & Dienste → Bibliothek** diese vier APIs **aktivieren**:
   - **Maps JavaScript API**
   - **Directions API**
   - **Geocoding API**
   - **Places API (New)**
3. Unter **APIs & Dienste → Anmeldedaten → Anmeldedaten erstellen → API-Schlüssel**
   einen Key erzeugen.
4. Den Key **einschränken** (wichtig, da er im Browser sichtbar ist):
   - **Anwendungsbeschränkungen → Websites (HTTP-Referrer):** deine Domain
     eintragen, z. B. `https://camper.example.com/*`. Zum lokalen Testen zusätzlich
     `http://localhost:8082/*`.
   - **API-Beschränkungen:** auf die vier oben genannten APIs begrenzen.

### 3. Projekt starten

```bash
# Repo holen
git clone https://github.com/<dein-user>/camper-reiseplaner.git
cd camper-reiseplaner

# Konfiguration anlegen und Key eintragen
cp .env.example .env
#   -> GOOGLE_MAPS_API_KEY=... in .env eintragen (Pflicht)

# Bauen & starten
docker compose up -d --build
```

Danach im Browser öffnen: **http://localhost:8082** (bzw. der in `.env` gesetzte
`CAMPER_PORT`). Läuft die App, kannst du sie als PWA installieren (dafür wird
HTTPS über eine Domain benötigt, siehe unten).

Health-Check: `curl http://localhost:8082/api/health` → `{"status":"ok"}`.

---

## ⚠️ Sicherheit: kein eingebautes Login

Diese App hat **bewusst keine Authentifizierung** – jeder, der die URL erreicht,
kann alle Reisen sehen und ändern. Das ist für ein privates Tool gedacht. **Stelle
die App niemals ungeschützt ins offene Internet.** Empfohlene Absicherung:

- Betrieb im Heimnetz oder über ein **VPN** (z. B. [Tailscale](https://tailscale.com/),
  WireGuard) – so betreibt sie der Autor.
- Oder ein **Reverse-Proxy mit Authentifizierung** davor (Basic-Auth,
  [Authelia](https://www.authelia.com/), oauth2-proxy, …).

Zusätzlich: den Google-Key **immer per HTTP-Referrer** auf deine Domain beschränken
(Schritt 2), damit er nicht von fremden Seiten missbraucht wird.

---

## HTTPS / Reverse-Proxy (empfohlen)

Für die **PWA-Installation** und den Service-Worker ist **HTTPS** nötig
(Ausnahme: `localhost`). Am einfachsten mit [Caddy](https://caddyserver.com/)
(automatische Let's-Encrypt-Zertifikate):

```caddy
# Caddyfile
camper.example.com {
    reverse_proxy localhost:8082
}
```

Mit **nginx** entsprechend ein `proxy_pass http://localhost:8082;` in einem
TLS-Server-Block (Zertifikat z. B. via certbot). Danach die Domain
(`https://camper.example.com/*`) in der Referrer-Beschränkung des Keys eintragen.

---

## Konfiguration (Umgebungsvariablen)

Alle Optionen stehen in `.env` (Vorlage: `.env.example`):

| Variable | Pflicht | Standard | Bedeutung |
|---|:---:|---|---|
| `GOOGLE_MAPS_API_KEY` | ✅ | – | Google-Maps-Key (Karte + Routing + Suche). Ohne ihn bleibt die Karte leer. |
| `CAMPER_PORT` | – | `8082` | Host-Port. Der Container lauscht intern immer auf `8000`. |

Intern (im Container gesetzt, normalerweise nicht anzufassen):
`CAMPER_DB=/data/camper.db` – Pfad der SQLite-Datei im Daten-Volume.

---

## Daten & Backup

Alle Daten liegen in **einer SQLite-Datei** im Docker-Volume `camper-data`
unter `/data/camper.db`.

**Konsistentes Backup** (Online-Snapshot, kein Stopp nötig):

```bash
# Snapshot innerhalb des Containers erzeugen …
docker compose exec camper python -c \
 "import sqlite3;s=sqlite3.connect('/data/camper.db');d=sqlite3.connect('/data/backup.db');s.backup(d);d.close();s.close()"
# … und herauskopieren
docker compose cp camper:/data/backup.db ./camper-backup-$(date +%F).db
```

**Wiederherstellen:**

```bash
docker compose cp ./camper-backup-2026-01-01.db camper:/data/camper.db
docker compose restart camper
```

Für regelmäßige Backups den ersten Befehl per Cron/Timer ausführen und die
`.db` an einen sicheren Ort sichern.

---

## Updates

```bash
git pull
docker compose up -d --build
```

Die Auto-Migration ergänzt neue DB-Spalten beim Start automatisch (bestehende
Daten bleiben erhalten). Der Service-Worker der PWA aktualisiert sich nach dem
Deploy selbstständig.

---

## Kosten

Google Maps Platform rechnet pro API-Aufruf ab, gewährt aber ein monatliches
**Gratis-Kontingent**, das für ein privates Tool praktisch immer ausreicht
(Größenordnung: tausende Kartenaufrufe und tausende Routen pro Monat kostenlos).
Bei ein paar Nutzern und gelegentlicher Planung landest du real bei **~0 €**.

Kostentreiber sind Karten-Neuladen, Routenberechnungen (je Etappe eine) und
Ortssuchen. Wer ganz sichergehen will, richtet in der Cloud Console ein
**Budget mit Alarm** ein und beschränkt den Key strikt auf die vier APIs.
Aktuelle Preise/Kontingente:
<https://mapsplatform.google.com/pricing/>.

---

## Entwicklung & Tests

**Backend lokal ohne Docker** (Python 3.12 empfohlen):

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
GOOGLE_MAPS_API_KEY=dein_key CAMPER_DB=./dev.db \
  uvicorn app.main:app --reload --port 8000
```

Das Frontend wird vom Backend unter `/` mit ausgeliefert – einfach
`http://localhost:8000` öffnen.

**Tests** (pytest):

```bash
cd backend
pip install -r requirements.txt pytest httpx
PYTHONPATH=. pytest -q
```

---

## API-Überblick

| Methode | Pfad | Zweck |
|---|---|---|
| GET | `/api/health` | Health-Check |
| GET | `/api/config` | liefert den Google-Maps-Key an das Frontend |
| GET / POST | `/api/trips` | Reisen listen / anlegen |
| GET / PATCH / DELETE | `/api/trips/{id}` | Reise lesen / ändern / löschen |
| GET / POST | `/api/trips/{id}/stops` | Stopps + POIs einer Reise |
| PUT | `/api/trips/{id}/stops/order` | Reihenfolge speichern |
| PATCH / DELETE | `/api/stops/{id}` | Stopp/POI ändern / löschen |

Interaktive API-Doku (FastAPI): `http://localhost:8082/docs`.

---

## Datenmodell & Erweiterbarkeit

- **Datenmodell:** `Trip` 1─n `Stop`. `Stop.kind` = `stop` (Übernachtung, immer in
  Liste/Route) oder `poi` (nur Punkt); `Stop.in_route` nimmt einen POI als Wegpunkt
  in die Route auf.
- **Neues Feld hinzufügen** ist bewusst einfach gehalten (eine Zeile im Modell +
  eine Zeile in der Frontend-Feldkonfiguration, DB-Spalte entsteht automatisch).
  Details und der Plan für Mandantenfähigkeit stehen in
  [`docs/ARCHITEKTUR.md`](docs/ARCHITEKTUR.md).

---

## Einschränkungen / Nicht-Ziele

- **Kein Login / Single-Tenant** – Zugriffsschutz erfolgt außerhalb der App
  (VPN/Reverse-Proxy).
- **Offline nur lesen** – ohne Netz gibt es keine Karte und kein Bearbeiten
  (die bereits geladenen Daten bleiben im Cache sichtbar).
- Karte/Routing/Suche sind an **Google Maps** gebunden (API-Key erforderlich).

---

## Lizenz

[MIT](LICENSE) © 2026 Oliver Porrmann. Nutzung, Änderung und Weitergabe frei –
ohne Gewährleistung.
