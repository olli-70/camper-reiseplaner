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

## Mandantenfähigkeit (geplant – NICHT umgesetzt)

Aktuell ist die App bewusst **Single-Tenant**: kein Login, Zugriffskontrolle
allein über Tailscale. Die Struktur ist aber schon mandantentauglich angelegt,
damit ein späterer Umbau **additiv** möglich ist – hier der Plan.

**Grundidee:** Ein *Mandant* (Haushalt/Nutzer) besitzt Reisen; Stopps hängen an
Reisen und erben die Zugehörigkeit. Die Reise ist also die Tenant-Grenze – die
gibt es heute schon, deshalb ist kein Umbau der Kernstruktur nötig.

**Wenn es so weit ist (Skizze, additiv):**

1. **Identität bestimmen** – eine FastAPI-Dependency `get_current_mandant()`.
   Heute gäbe es keine Quelle; später z. B. aus einem vom Reverse-Proxy
   gesetzten Auth-Header (Pocket-ID/OIDC-`sub`) oder aus der Tailscale-Identität.
   Bis dahin könnte sie einen festen Wert `"default"` liefern.
2. **Spalte ergänzen** – `mandant_id` an `Trip` (erst NULL-fähig → Bestand auf
   `"default"` backfillen → verpflichtend). Die Auto-Migration legt die Spalte an.
3. **Abfragen scopen** – in `main.py` alle Trip-Endpunkte per
   `WHERE mandant_id = :aktueller_mandant` filtern; Stopp-Endpunkte prüfen die
   Zugehörigkeit über die Reise. Das ist rein additiv (ein `WHERE` mehr).
4. **Frontend** – i. d. R. keine Änderung; nur falls ein Nutzer mehrere
   Mandanten hat, käme ein Mandanten-Umschalter dazu.

**Warum aufgeschoben:** Es gibt (noch) keinen zweiten Haushalt, und Tailscale
regelt den Zugang. YAGNI – erst bauen, wenn ein echter zweiter Mandant existiert.

**Was heute schon beachtet ist:** Die API ist bereits reise-zentriert
(`/api/trips/{id}/stops`), Daten hängen unter Reisen. Dadurch bleibt der
Tenant-Filter später ein additiver Zusatz und kein Umbau.
