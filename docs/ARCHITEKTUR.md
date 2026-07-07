# Architektur & Erweiterbarkeit

Kurzer Leitfaden, wie der Camper-Reiseplaner erweitert wird – bewusst schlank
gehalten (Single-User hinter Tailscale, FastAPI + SQLite + Vanilla-PWA).

## Überblick

```
Browser (PWA, MapLibre)  ──REST──►  FastAPI  ──►  SQLite (/data/camper.db)
        app.js / index.html               app/main.py, models.py, db.py
```

- **Ein Container** serviert API + statische PWA.
- **Datenmodell:** `Trip` 1─n `Stop` (ein Stopp gehört zu genau einer Reise).

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
   `showIf: "<checkbox-key>"` (Feld nur sichtbar, wenn die Checkbox an ist).

3. **Deployen** wie üblich (Playbook `camper-reiseplaner.yml`) und den
   Service-Worker-Cache in `frontend/sw.js` hochzählen (`camper-vN`), damit die
   PWA die neue Version lädt.

> **Popup/Karte:** Die Marker-Popup-Anzeige ist bewusst *kuratiert*
> (`stopPopupDOM` in `app.js`). Soll ein neues Feld dort erscheinen, wird es
> dort gezielt ergänzt – das ist Anzeige, keine Dateneingabe.

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
