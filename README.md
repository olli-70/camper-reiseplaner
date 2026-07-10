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

## ⚡ Schnellstart in 5 Minuten

**1. Google-Maps-Key erstellen** (in der
[Google Cloud Console](https://console.cloud.google.com/)):
Projekt anlegen → Rechnungskonto verknüpfen → unter **APIs & Dienste → Bibliothek**
diese vier APIs aktivieren: **Maps JavaScript API**, **Directions API**,
**Geocoding API**, **Places API (New)** → unter **Anmeldedaten** einen
**API-Schlüssel** erstellen → den Key per **HTTP-Referrer** auf deine Domain
beschränken (z. B. `https://camper.example.com/*`, fürs lokale Testen zusätzlich
`http://localhost:8082/*`).

**2. Mit Docker starten:**

```bash
git clone https://github.com/<dein-user>/camper-reiseplaner.git
cd camper-reiseplaner
cp .env.example .env          # GOOGLE_MAPS_API_KEY, SESSION_SECRET, ADMIN_* eintragen
docker compose up -d --build
```

In `.env` mindestens setzen: `GOOGLE_MAPS_API_KEY` (Karte), `SESSION_SECRET`
(Cookie-Signatur) sowie `ADMIN_USER` + `ADMIN_PASSWORD` (dein Login).

Fertig → im Browser **http://localhost:8082** öffnen (Port über `CAMPER_PORT`
in `.env` änderbar), mit dem Admin-Konto anmelden.

> Ausführlicher – Key-Beschränkung, HTTPS für die PWA, weitere Konten, Backup –
> steht weiter unten.
> **Login vorhanden:** die App schützt sich selbst per E-Mail/Passwort und trennt
> die Reisen je Konto. Trotzdem gehört sie hinter **HTTPS** (das Login-Cookie ist
> standardmäßig `Secure`) – siehe [Sicherheit](#️-sicherheit--login).

---

## Inhalt

- [⚡ Schnellstart in 5 Minuten](#-schnellstart-in-5-minuten)
- [Funktionen](#funktionen)
- [Technik & Architektur](#technik--architektur)
- [Schnellstart (Docker Compose)](#schnellstart-docker-compose)
  - [1. Voraussetzungen](#1-voraussetzungen)
  - [2. Google-Maps-API-Key besorgen](#2-google-maps-api-key-besorgen)
  - [3. Projekt starten](#3-projekt-starten)
- [⚠️ Sicherheit & Login](#️-sicherheit--login)
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

**Konten & Login**
- Eingebauter **Login per E-Mail + Passwort** (Session-Cookie), kein externer
  Dienst nötig. In der Kopfzeile: Profil-Button mit angemeldeter E-Mail und
  **Abmelden** sowie die **App-Version**.
- **Getrennte Konten**: jeder Nutzer sieht **nur seine eigenen Reisen**.
- **Admin-Konto** wird aus der Konfiguration geseedet; weitere Nutzer werden über
  eine Whitelist mit persönlichem **Einmalcode** freigeschaltet (setzen damit ihr
  Passwort selbst). Kein offenes Registrieren.
- **CSV-Export**: eigene Reisen (Übernachtungsplätze + POIs) als CSV herunterladen
  (Excel/Numbers-tauglich) — **alle** über das Profil-Menü oder **einzeln** über die
  Tour-Einstellungen (⚙️).

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
   - **Maps JavaScript API** (Karte im Browser)
   - **Directions API**, **Geocoding API**, **Places API (New)** (laufen server-seitig)
3. Am besten **zwei Keys** anlegen (Trennung Browser ↔ Server). Für einen schnellen
   Start genügt der Render-Key allein – der Server nutzt ihn dann als Fallback.
   - **Render-Key** (`GOOGLE_MAPS_API_KEY`, **Pflicht**, im Browser sichtbar):
     **API-Beschränkung nur auf „Maps JavaScript API"**, **Anwendungsbeschränkung
     HTTP-Referrer** auf deine Domain (z. B. `https://camper.example.com/*`, lokal
     zusätzlich `http://localhost:8082/*`).
   - **Server-Key** (`GOOGLE_MAPS_SERVER_KEY`, empfohlen, erreicht den Browser nie):
     API-Beschränkung auf Directions/Geocoding/Places (New), **Anwendungsbeschränkung
     per IP-Adresse** auf deinen Server – **keine** Referrer-Beschränkung (die
     Legacy-Webdienste lehnen referrer-beschränkte Keys ab).

### 3. Projekt starten

```bash
# Repo holen
git clone https://github.com/<dein-user>/camper-reiseplaner.git
cd camper-reiseplaner

# Konfiguration anlegen und ausfüllen
cp .env.example .env
#   Pflicht in .env: GOOGLE_MAPS_API_KEY (Karte), SESSION_SECRET (Cookie-Signatur),
#   ADMIN_USER + ADMIN_PASSWORD (dein Login). Weitere Nutzer über MEMBERS.

# Bauen & starten
docker compose up -d --build
```

Danach im Browser öffnen: **http://localhost:8082** (bzw. der in `.env` gesetzte
`CAMPER_PORT`). Läuft die App, kannst du sie als PWA installieren (dafür wird
HTTPS über eine Domain benötigt, siehe unten).

Health-Check: `curl http://localhost:8082/api/health` → `{"status":"ok"}`.

---

## ⚠️ Sicherheit & Login

Die App bringt einen **eigenen Login** mit (E-Mail + Passwort, signiertes
Session-Cookie) und trennt die Reisen je Konto – sie darf also mit Zugangsschutz
ins Internet. Beim Betrieb beachten:

- **`SESSION_SECRET` setzen** (zufälliger Wert, z. B.
  `python -c "import secrets; print(secrets.token_hex(32))"`). Ohne eigenen Wert
  greift ein unsicherer Default – niemals in Produktion so lassen.
- **HTTPS verwenden.** Das Login-Cookie ist standardmäßig `Secure` (nur über HTTPS).
  Für rein lokales HTTP-Testen `COOKIE_SECURE=0` setzen.
- **Konten:** `ADMIN_USER`/`ADMIN_PASSWORD` seeden das Admin-Konto; weitere Nutzer
  nur über die **`MEMBERS`-Whitelist** mit persönlichem Einmalcode. Es gibt **kein
  offenes Registrieren** – wer nicht in `MEMBERS` (oder Admin) steht, kommt nicht rein.
- **Passwörter** werden mit **bcrypt** gehasht; ein einfacher **Rate-Limiter**
  (`LOGIN_RATELIMIT`) bremst Brute-Force pro IP.
- **Render-Key** zusätzlich per **HTTP-Referrer** auf deine Domain beschränken
  (Schritt 2), damit er nicht von fremden Seiten missbraucht wird.

Wer die App lieber ganz privat betreibt, kann sie weiterhin zusätzlich ins Heimnetz
oder hinter ein **VPN** (z. B. [Tailscale](https://tailscale.com/), WireGuard) legen.

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
| `GOOGLE_MAPS_API_KEY` | ✅ | – | Render-Key (Karte im Browser). Ohne ihn bleibt die Karte leer. |
| `GOOGLE_MAPS_SERVER_KEY` | – | Render-Key | Server-Key für Directions/Places/Geocoding (erreicht den Browser nie). Leer = Fallback auf den Render-Key. |
| `GOOGLE_KEY_REFERER` | – | – | `Referer`-Header, den der Server mitschickt (nur nötig, wenn der Server-Key referrer-beschränkt ist). |
| `SESSION_SECRET` | ✅ | *unsicherer Default* | Signaturschlüssel des Login-Cookies. In Produktion zwingend eigenen Zufallswert setzen. |
| `ADMIN_USER` | ✅ | – | E-Mail des Admin-Kontos (wird beim Start geseedet; erbt herrenlose Bestandsreisen). |
| `ADMIN_PASSWORD` | ✅ | – | Passwort des Admin-Kontos (Quelle der Wahrheit, folgt der Konfiguration). |
| `MEMBERS` | – | `[]` | Weitere Nutzer als JSON `[{"email":…,"code":…}]`. Nur diese (plus Admin) dürfen rein; `code` = persönlicher Einmalcode zum Passwort-Setzen. |
| `COOKIE_SECURE` | – | `1` | Login-Cookie nur über HTTPS senden. Für lokales HTTP-Testen auf `0`. |
| `LOGIN_RATELIMIT` | – | `20` | Login-Versuche pro IP je 5-Minuten-Fenster. |
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
  SESSION_SECRET=dev COOKIE_SECURE=0 \
  ADMIN_USER=admin@example.de ADMIN_PASSWORD=geheim1234 \
  uvicorn app.main:app --reload --port 8000
```

Das Frontend wird vom Backend unter `/` mit ausgeliefert – einfach
`http://localhost:8000` öffnen und mit dem Admin-Konto anmelden
(`COOKIE_SECURE=0`, weil lokal ohne HTTPS).

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
| GET | `/api/config` | liefert Render-Key + App-Version an das Frontend |
| POST | `/api/auth/login` · `/api/auth/set-password` · `/api/auth/logout` | Anmelden / Passwort per Einmalcode setzen / Abmelden |
| GET | `/api/auth/me` | angemeldeten Nutzer (E-Mail) abfragen |
| GET | `/api/export.csv` | alle eigenen Reisen (Übernachtungsplätze + POIs) als CSV |
| GET | `/api/trips/{id}/export.csv` | eine einzelne eigene Reise als CSV |
| POST | `/api/directions` · `/api/places` · GET `/api/geocode` | Google-Web-Dienste server-seitig (Key bleibt am Server) |
| GET / POST | `/api/trips` | Reisen listen / anlegen (nur eigene) |
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
  Details sowie Login & Daten-Isolation (Mandantenfähigkeit) stehen in
  [`docs/ARCHITEKTUR.md`](docs/ARCHITEKTUR.md).

---

## Einschränkungen / Nicht-Ziele

- **Konten per Whitelist, kein offenes Registrieren** – neue Nutzer werden über
  `MEMBERS` (E-Mail + Einmalcode) freigeschaltet, nicht per Self-Service.
- **Kein Teilen zwischen Konten** – Reisen sind je Konto isoliert; ein Haushalt
  teilt sich sinnvollerweise **ein** Konto.
- **Offline nur lesen** – ohne Netz gibt es keine Karte und kein Bearbeiten
  (die bereits geladenen Daten bleiben im Cache sichtbar).
- Karte/Routing/Suche sind an **Google Maps** gebunden (API-Key erforderlich).

---

## Lizenz

[MIT](LICENSE) © 2026 Oliver Porrmann. Nutzung, Änderung und Weitergabe frei –
ohne Gewährleistung.
