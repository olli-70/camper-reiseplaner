# Architektur & Erweiterbarkeit

Kurzer Leitfaden, wie der Camper-Reiseplaner erweitert wird – bewusst schlank
gehalten (Single-User, FastAPI + SQLite + Vanilla-PWA). Deploy & Betrieb stehen
im [README](../README.md).

## Überblick

```
Browser (PWA, Google Maps)  ──REST──►  FastAPI  ──►  SQLite (/data/camper.db)
        app.js / index.html                 app/main.py, models.py, db.py
```

- **Ein Container** serviert API + statische PWA.
- **Datenmodell:** `Trip` 1─n `Stop`. `Stop.kind` unterscheidet
  `stop` (Übernachtungsplatz, immer in Liste/Route) und `poi` (nur Punkt).
  `Stop.in_route` (nur POIs): POI als **Wegpunkt** in die Route aufnehmen –
  er erscheint dann sortierbar in der Liste und liegt auf der Routenlinie.
- **Route (etappenweise):** `state.route` = Übernachtungen + `in_route`-POIs,
  gemeinsam nach `reihenfolge` sortiert. Anker = Start, jede Übernachtung, Ziel;
  zwischen zwei Ankern liegt eine **Etappe**, deren `in_route`-POIs zu Wegpunkten
  werden. Pro Etappe **eine eigene Google-Directions-Anfrage** + eine eigene,
  abwechselnd gefärbte `google.maps.Polyline` (`routePolylines[]`). Dadurch greift
  das 25-Wegpunkt-Limit nur je Etappe (praktisch nie) und km/Zeit sind pro Etappe
  sichtbar: Übernachtungs-Zeile = Etappen-Summe, POI-Zeile = einzelner Umweg-Sprung.
- **Externe Dienste (gratis-Kontingent):** Google Maps JS (Basiskarte + Directions,
  Key als ENV `GOOGLE_MAPS_API_KEY` → `/api/config`, Referrer-beschränkt), OSRM Demo
  (`/table` = POI→alle Plätze im POI-Popup), Google Places/Geocoder + Nominatim
  (Ortssuche/Reverse-Geocoding). Kein Dienst wird serverseitig gebraucht.
- **Backup:** SQLite-Online-Snapshot (kein Stopp nötig); Befehle im
  [README → Daten & Backup](../README.md#daten--backup).

## Ein neues Feld an einem Stopp hinzufügen

Dank Auto-Migration (Backend) und Feld-Konfiguration (Frontend) sind nur **zwei
kleine Stellen** nötig:

1. **Backend – Feld ins Modell** (`backend/app/models.py`):
   - Spalte in der Tabelle `Stop` ergänzen, z. B. `preis: Optional[float] = None`.
   - Falls das Feld über die API setzbar sein soll, dieselbe Zeile in
     `StopCreate` **und** `StopUpdate` ergänzen.
   - Beim nächsten Start legt `db.py::_migrate()` die DB-Spalte **automatisch**
     an (`ALTER TABLE … ADD COLUMN`, NULL-fähig – Bestandsdaten bleiben).

2. **Frontend – Feld in die Konfiguration** (`frontend/app.js`, `STOP_FIELDS`):
   ```js
   { key: "preis", label: "Preis (€)", type: "text" },
   ```
   Formular-Aufbau, Vorbelegung beim Bearbeiten und Speichern laufen generisch
   über `STOP_FIELDS` – kein weiterer Code nötig.

   Feldtypen: `text | textarea | select | date | datetime | checkbox`.
   Optional: `options` (bei `select`), `default`, `required`,
   `showIf: "<checkbox-key>"` (Feld nur sichtbar, wenn die Checkbox an ist),
   `poi: true` (Feld auch beim **POI**-Bearbeiten zeigen; ohne die Flag erscheint
   es nur bei Übernachtungsplätzen – POIs zeigen sonst nur Name + Notiz).

3. **Neu bauen/deployen** (`docker compose up -d --build`) und den
   Service-Worker-Cache in `frontend/sw.js` hochzählen (`camper-vN`), damit die
   PWA die neue Version lädt.

> **Popup/Karte:** Die Marker-InfoWindows sind bewusst *kuratiert*
> (`stopPopupDOM` für Übernachtungsplätze, `openPoiInfo` für POIs in `app.js`).
> Soll ein neues Feld dort erscheinen, wird es gezielt ergänzt – das ist Anzeige,
> keine Dateneingabe.

## Login & Mandantenfähigkeit (umgesetzt)

Die App hat einen **eingebauten Login** und trennt die Daten je Konto – die
Reise ist die Tenant-Grenze, ihr Eigentümer der Nutzer.

**Authentifizierung:**
- `User` (E-Mail unique, `password_hash` per **bcrypt**, `is_admin`). Anmeldung
  über `/api/auth/login`; Session in einem **signierten HttpOnly-Cookie**
  (Starlette `SessionMiddleware`, `SESSION_SECRET`, standardmäßig `Secure`).
- **Kein offenes Registrieren:** Nur E-Mails aus der `MEMBERS`-Whitelist (plus
  `ADMIN_USER`) dürfen rein (`_allowed()`), geprüft bei **jeder** Anfrage – ein
  Entzug wirkt sofort. Neue Nutzer setzen ihr Passwort einmalig per persönlichem
  **Einmalcode** (`/api/auth/set-password`, Code nach Gebrauch verbraucht).
- Der **Admin** wird beim Start aus `ADMIN_USER`/`ADMIN_PASSWORD` geseedet
  (`_seed_admin`); ändert sich die Admin-E-Mail, wird das bestehende Konto
  umbenannt (Reisen bleiben). Ein einfacher IP-**Rate-Limiter** bremst Brute-Force.

**Daten-Isolation:**
- `Trip.user_id` (FK, indiziert; die Auto-Migration ergänzt die Spalte, herrenlose
  Bestandsreisen gehen per Backfill an den Admin).
- `get_current_user()` liefert den angemeldeten Nutzer; alle Trip-Endpunkte sind
  auf `user_id` gescoped, Stopp-Endpunkte prüfen die Zugehörigkeit über die Reise
  (`_owned_trip` → 404 bei Fremdzugriff).

**Nutzerliste synchronisieren (`reconcile_members`):**
- Änderungen an `MEMBERS` werden beim (Re-)Start eingelesen. **Neue** Codes wirken
  sofort; einen Nutzer **entfernen** heißt: aus `MEMBERS` nehmen **und** den Sync
  auslösen – dann wird sein Konto **inklusive Reisen und Stopps gelöscht**.
- `reconcile_members()` löscht alle DB-Nutzer, die weder Admin noch (mehr) in
  `MEMBERS` stehen. **Schutz:** `_parse_members_strict()` bricht bei kaputter/leerer
  E-Mail oder leerem Code ab → dann wird **nichts** gelöscht; der Admin ist immer
  ausgenommen.
- Ausgelöst wird der Sync **explizit** (nicht bei jedem Start), per CLI im Container:
  ```bash
  docker exec camper-reiseplaner python -m app.main reconcile-members
  ```
  Ausgabe = JSON-Report (`deleted` / `kept_allowed`), Exit-Code ≠ 0 bei Fehler.
- Passwort vergessen? Neuen Einmalcode in `MEMBERS` eintragen und den Container mit
  frischer `MEMBERS`-Umgebung neu starten – der Nutzer setzt damit ein neues Passwort,
  **seine Reisen bleiben** (gleiches Konto).

**Google-Web-Dienste server-seitig:** Directions/Places/Geocoding laufen im Backend
(`/api/directions`, `/api/places`, `/api/geocode`) mit dem `GOOGLE_MAPS_SERVER_KEY`;
nur der referrer-beschränkte Render-Key erreicht den Browser.
