import json
import os
import tempfile

# Test-Umgebung setzen, BEVOR die App-Module importiert werden
_fd, _path = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["CAMPER_DB"] = _path
os.environ["SESSION_SECRET"] = "test-session-secret-0123456789abcdef"  # >=32 (S2-Guard)
os.environ["COOKIE_SECURE"] = "0"  # TestClient läuft über http
os.environ["LOGIN_RATELIMIT"] = "1000"  # im Test nicht limitieren
CODES = {"a@test.de": "code-a", "b@test.de": "code-b", "c@test.de": "code-c",
         "d@test.de": "code-d"}  # d nur für den Session-Revocation-Test (S6)
_MEMBERS = [{"email": e, "code": c} for e, c in CODES.items()]
os.environ["MEMBERS"] = json.dumps(_MEMBERS)

from fastapi.testclient import TestClient  # noqa: E402

from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402

init_db()


def _client(email):
    """TestClient mit eigenem Cookie-Jar, per Einmalcode angemeldet."""
    c = TestClient(app)
    r = c.post("/api/auth/set-password",
               json={"email": email, "code": CODES[email], "password": "password1"})
    assert r.status_code == 200, r.text
    return c


# angemeldeter Standard-Client für die meisten Tests
client = _client("a@test.de")


def test_setpw_needs_valid_code():
    c = TestClient(app)
    # falscher Code
    assert c.post("/api/auth/set-password",
                  json={"email": "b@test.de", "code": "falsch", "password": "password1"}
                  ).status_code == 403
    # E-Mail nicht in der member-Liste
    assert c.post("/api/auth/set-password",
                  json={"email": "fremd@test.de", "code": "x", "password": "password1"}
                  ).status_code == 403


def test_onetime_code_and_reset():
    # erstmalig setzen -> ok
    assert TestClient(app).post("/api/auth/set-password",
                                json={"email": "c@test.de", "code": "code-c", "password": "password1"}
                                ).status_code == 200
    # gleicher Code nochmal -> 409 (verbraucht)
    assert TestClient(app).post("/api/auth/set-password",
                                json={"email": "c@test.de", "code": "code-c", "password": "hijack99"}
                                ).status_code == 409
    # Login mit erstem Passwort geht
    assert TestClient(app).post("/api/auth/login",
                                json={"email": "c@test.de", "password": "password1"}).status_code == 200
    # Reset: neuer Code wird hinterlegt -> Passwort neu setzbar
    os.environ["MEMBERS"] = json.dumps([{"email": "c@test.de", "code": "code-c2"}])
    try:
        assert TestClient(app).post("/api/auth/set-password",
                                    json={"email": "c@test.de", "code": "code-c2", "password": "password3"}
                                    ).status_code == 200
        # altes Passwort funktioniert nicht mehr
        assert TestClient(app).post("/api/auth/login",
                                    json={"email": "c@test.de", "password": "password1"}).status_code == 401
    finally:
        os.environ["MEMBERS"] = json.dumps(_MEMBERS)  # Umgebung wiederherstellen


def test_health():
    assert TestClient(app).get("/api/health").json() == {"status": "ok"}


def test_requires_auth():
    anon = TestClient(app)
    assert anon.get("/api/trips").status_code == 401
    assert anon.get("/api/config").status_code == 401


def test_login_and_me():
    c = TestClient(app)
    assert c.post("/api/auth/login",
                  json={"email": "a@test.de", "password": "password1"}).status_code == 200
    assert c.get("/api/auth/me").json()["email"] == "a@test.de"


def test_login_only_listed_emails():
    # S7: keine Account-Enumeration – nicht freigeschaltete E-Mail liefert dieselbe
    # 401-Antwort wie "falsches Passwort" (nicht 403), damit man Member nicht per
    # Statuscode unterscheiden kann.
    r_unlisted = TestClient(app).post(
        "/api/auth/login", json={"email": "fremd@test.de", "password": "whatever8"})
    r_wrongpw = TestClient(app).post(
        "/api/auth/login", json={"email": "a@test.de", "password": "totally-wrong"})
    assert r_unlisted.status_code == 401
    assert r_wrongpw.status_code == 401
    # Identische Fehlermeldung (kein Text-Orakel)
    assert r_unlisted.json() == r_wrongpw.json()


def test_isolation_between_accounts():
    a = client
    b = _client("b@test.de")
    tid = a.post("/api/trips", json={"name": "A's Reise"}).json()["id"]
    # B sieht A's Reise nicht in der Liste
    assert all(t["id"] != tid for t in b.get("/api/trips").json())
    # B kann A's Reise nicht direkt lesen / ändern / löschen (404)
    assert b.get(f"/api/trips/{tid}").status_code == 404
    assert b.patch(f"/api/trips/{tid}", json={"name": "hijack"}).status_code == 404
    assert b.delete(f"/api/trips/{tid}").status_code == 404
    assert b.get(f"/api/trips/{tid}/stops").status_code == 404
    a.delete(f"/api/trips/{tid}")


def test_config_has_maps_key_field():
    r = client.get("/api/config")
    assert r.status_code == 200
    assert "googleMapsApiKey" in r.json()


def test_trip_touched_when_stop_added():
    import time
    tid = client.post("/api/trips", json={"name": "Touch-Test"}).json()["id"]
    before = client.get(f"/api/trips/{tid}").json()["updated_at"]
    time.sleep(0.01)
    client.post(f"/api/trips/{tid}/stops", json={"name": "S", "lat": 1, "lng": 2})
    after = client.get(f"/api/trips/{tid}").json()["updated_at"]
    assert after > before
    client.delete(f"/api/trips/{tid}")


def test_poi_kind():
    tid = client.post("/api/trips", json={"name": "POI"}).json()["id"]
    r = client.post(f"/api/trips/{tid}/stops", json={"name": "Aussicht", "lat": 1, "lng": 2, "kind": "poi"})
    assert r.status_code == 201
    assert r.json()["kind"] == "poi"
    r2 = client.post(f"/api/trips/{tid}/stops", json={"name": "Platz", "lat": 3, "lng": 4})
    assert r2.json()["kind"] == "stop"
    client.delete(f"/api/trips/{tid}")


def test_kind_conversion_both_ways():
    """POI <-> Übernachtungsplatz per PATCH umwandeln (Basis für den Umwandeln-Button)."""
    tid = client.post("/api/trips", json={"name": "Umwandeln"}).json()["id"]
    # als POI anlegen -> in Übernachtungsplatz umwandeln
    sid = client.post(
        f"/api/trips/{tid}/stops", json={"name": "Spot", "lat": 1, "lng": 2, "kind": "poi"}
    ).json()["id"]
    assert client.patch(f"/api/stops/{sid}", json={"kind": "stop"}).json()["kind"] == "stop"
    # und wieder zurück zum POI
    assert client.patch(f"/api/stops/{sid}", json={"kind": "poi"}).json()["kind"] == "poi"
    client.delete(f"/api/trips/{tid}")


def test_create_stop_with_reservation():
    """Neuer Übernachtungsplatz inkl. Reservierung in EINEM POST (Feature: Reservierung
    direkt bei Neuanlage erfassen)."""
    tid = client.post("/api/trips", json={"name": "Reservierung"}).json()["id"]
    r = client.post(
        f"/api/trips/{tid}/stops",
        json={
            "name": "Camping", "lat": 5, "lng": 6, "kind": "stop", "status": "reserviert",
            "reserviert": True,
            "reserviert_von": "2026-08-01T14:00", "reserviert_bis": "2026-08-03T11:00",
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["kind"] == "stop"
    assert body["reserviert"] is True
    assert body["reserviert_von"].startswith("2026-08-01T14:00")
    assert body["reserviert_bis"].startswith("2026-08-03T11:00")
    client.delete(f"/api/trips/{tid}")


def test_times_saved_without_reservation():
    """An/Ab-Zeiten müssen OHNE gesetztes reserviert-Flag gespeichert werden
    (Regressionsschutz: früher wurden sie beim Speichern genullt)."""
    tid = client.post("/api/trips", json={"name": "Zeiten"}).json()["id"]
    # Neuanlage: Zeiten ohne reserviert
    r = client.post(
        f"/api/trips/{tid}/stops",
        json={"name": "Platz", "lat": 1, "lng": 2, "kind": "stop", "reserviert": False,
              "reserviert_von": "2026-09-01T15:30", "reserviert_bis": "2026-09-02T10:00"},
    )
    assert r.status_code == 201
    body = r.json()
    sid = body["id"]
    assert body["reserviert"] is False
    assert body["reserviert_von"].startswith("2026-09-01T15:30")
    assert body["reserviert_bis"].startswith("2026-09-02T10:00")
    # Änderung: Zeiten per PATCH aktualisieren, weiterhin ohne reserviert
    r2 = client.patch(f"/api/stops/{sid}",
                      json={"reserviert_von": "2026-09-01T16:00"})
    assert r2.status_code == 200
    assert r2.json()["reserviert_von"].startswith("2026-09-01T16:00")
    # nach erneutem Laden noch da?
    got = client.get(f"/api/trips/{tid}/stops").json()[0]
    assert got["reserviert_von"].startswith("2026-09-01T16:00")
    assert got["reserviert_bis"].startswith("2026-09-02T10:00")
    client.delete(f"/api/trips/{tid}")


def test_poi_datetime():
    """Ein POI kann einen Zeitpunkt (reserviert_von = Datum & Uhrzeit) tragen."""
    tid = client.post("/api/trips", json={"name": "POI-Zeit"}).json()["id"]
    r = client.post(
        f"/api/trips/{tid}/stops",
        json={"name": "Aussicht", "lat": 1, "lng": 2, "kind": "poi",
              "reserviert_von": "2026-09-01T12:00"},
    )
    assert r.status_code == 201
    assert r.json()["kind"] == "poi"
    assert r.json()["reserviert_von"].startswith("2026-09-01T12:00")
    client.delete(f"/api/trips/{tid}")


def test_new_stops_appended_at_end():
    """Neue Stopps werden ans Ende der Liste einsortiert (reihenfolge = max+1)."""
    tid = client.post("/api/trips", json={"name": "Anhaengen"}).json()["id"]
    names = ["Erster", "Zweiter", "Dritter"]
    for n in names:
        client.post(f"/api/trips/{tid}/stops", json={"name": n, "lat": 1, "lng": 2})
    got = client.get(f"/api/trips/{tid}/stops").json()
    assert [s["name"] for s in got] == names          # Reihenfolge = Anlage-Reihenfolge
    assert [s["reihenfolge"] for s in got] == [0, 1, 2]
    client.delete(f"/api/trips/{tid}")


def test_reorder_stops():
    tid = client.post("/api/trips", json={"name": "Reorder"}).json()["id"]
    ids = [
        client.post(f"/api/trips/{tid}/stops", json={"name": n, "lat": 1, "lng": 2}).json()["id"]
        for n in ["A", "B", "C"]
    ]
    new_order = [ids[2], ids[0], ids[1]]
    r = client.put(f"/api/trips/{tid}/stops/order", json={"order": new_order})
    assert r.status_code == 200
    assert [s["id"] for s in r.json()] == new_order
    got = client.get(f"/api/trips/{tid}/stops").json()
    assert [s["id"] for s in got] == new_order
    client.delete(f"/api/trips/{tid}")


def test_trip_and_stop_lifecycle():
    r = client.post("/api/trips", json={"name": "Norwegen 2026"})
    assert r.status_code == 201
    trip_id = r.json()["id"]
    r = client.post(
        f"/api/trips/{trip_id}/stops",
        json={"name": "Preikestolen", "lat": 58.98, "lng": 6.19, "status": "geplant"},
    )
    assert r.status_code == 201
    stop_id = r.json()["id"]
    r = client.patch(f"/api/stops/{stop_id}", json={"status": "besucht"})
    assert r.status_code == 200
    assert r.json()["status"] == "besucht"
    r = client.patch(
        f"/api/stops/{stop_id}",
        json={"reserviert": True, "reserviert_von": "2026-07-10T14:00",
              "reserviert_bis": "2026-07-12T11:00"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["reserviert"] is True
    assert body["reserviert_von"].startswith("2026-07-10T14:00")
    assert client.patch(f"/api/stops/{stop_id}", json={"status": "quatsch"}).status_code == 422
    stops = client.get(f"/api/trips/{trip_id}/stops").json()
    assert len(stops) == 1
    assert client.delete(f"/api/stops/{stop_id}").status_code == 204
    assert client.delete(f"/api/trips/{trip_id}").status_code == 204


# ---- Umkreis-Stellplätze (OSM/Overpass, server-seitig proxied) ---------------
# Aufgezeichneter Overpass-Response (node + way mit center + unbrauchbares
# Element) -> wir mocken _overpass_query, damit im Test NIE echt Overpass läuft.
import app.routers.campsites as campsites_mod  # noqa: E402  (C1: _overpass_query lebt jetzt hier)
from fastapi import HTTPException  # noqa: E402

_RECORDED_OVERPASS = {
    "elements": [
        {"type": "node", "id": 1, "lat": 52.5, "lon": 13.4,
         "tags": {"tourism": "caravan_site", "name": "Stellplatz Mitte",
                  "fee": "yes", "capacity": "20", "sanitary_dump_station": "yes",
                  "website": "example.com", "addr:city": "Berlin", "junk": "weg"}},
        {"type": "way", "id": 2, "center": {"lat": 52.6, "lon": 13.5},
         "tags": {"tourism": "caravan_site"}},          # ohne Name -> Fallback
        {"type": "node", "id": 3, "lat": None, "lon": None, "tags": {}},  # -> gefiltert
    ]
}


def test_campsites_nearby_requires_auth():
    anon = TestClient(app)
    assert anon.post("/api/campsites-nearby",
                     json={"lat": 52.5, "lng": 13.4}).status_code == 401


def test_campsites_nearby_parses_overpass(monkeypatch):
    async def fake_overpass(query):
        assert 'tourism"="caravan_site' in query
        assert "around:25000" in query          # Default-Radius 25 km
        assert "out center" in query            # Center für ways/relations
        return _RECORDED_OVERPASS

    monkeypatch.setattr(campsites_mod, "_overpass_query", fake_overpass)
    r = client.post("/api/campsites-nearby", json={"lat": 52.51, "lng": 13.41})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 2                   # unbrauchbares Element gefiltert
    assert body["radius"] == 25000
    first = body["campsites"][0]
    assert first["name"] == "Stellplatz Mitte"
    assert first["tags"]["fee"] == "yes"
    assert first["tags"]["capacity"] == "20"
    assert first["tags"]["sanitary_dump_station"] == "yes"
    assert "junk" not in first["tags"]          # nur relevante Tags durchgereicht
    second = body["campsites"][1]
    assert second["lat"] == 52.6 and second["lng"] == 13.5   # center übernommen
    assert second["name"] == "Wohnmobil-Stellplatz"          # Fallback ohne name


def test_campsites_nearby_radius_clamped(monkeypatch):
    captured = {}

    async def fake_overpass(query):
        captured["q"] = query
        return {"elements": []}

    monkeypatch.setattr(campsites_mod, "_overpass_query", fake_overpass)
    r = client.post("/api/campsites-nearby",
                    json={"lat": 48.1, "lng": 11.6, "radius": 999999})
    assert r.status_code == 200
    assert r.json()["radius"] == 50000          # auf Max gedeckelt
    assert "around:50000" in captured["q"]


def test_campsites_nearby_validation():
    assert client.post("/api/campsites-nearby", json={"lat": 52.5}).status_code == 422
    assert client.post("/api/campsites-nearby",
                       json={"lat": 999, "lng": 13.4}).status_code == 422


def test_campsites_nearby_overpass_down(monkeypatch):
    async def boom(query):
        raise HTTPException(502, "Overpass nicht erreichbar")

    monkeypatch.setattr(campsites_mod, "_overpass_query", boom)
    r = client.post("/api/campsites-nearby", json={"lat": 40.0, "lng": 9.0})
    assert r.status_code == 502


# ---- S4: Security-Header / CSP ----------------------------------------------
def test_security_headers_present():
    r = TestClient(app).get("/api/health")
    h = r.headers
    assert "content-security-policy" in h
    csp = h["content-security-policy"]
    assert "frame-ancestors 'none'" in csp
    assert "https://maps.googleapis.com" in csp          # Karte darf laden
    assert "https://nominatim.openstreetmap.org" in csp  # Ortssuche (connect-src)
    assert h.get("x-content-type-options") == "nosniff"
    assert h.get("x-frame-options") == "DENY"
    assert "referrer-policy" in h


# ---- S6: Session-Widerruf über logout-all -----------------------------------
def test_logout_all_revokes_other_sessions():
    # zwei Sitzungen desselben Nutzers (d@test.de, nur hier verwendet)
    c1 = _client("d@test.de")                     # set-password -> Session A
    c2 = TestClient(app)
    assert c2.post("/api/auth/login",
                   json={"email": "d@test.de", "password": "password1"}).status_code == 200
    assert c1.get("/api/auth/me").status_code == 200
    assert c2.get("/api/auth/me").status_code == 200
    # c2 meldet sich "überall" ab -> token_version++ -> c1s Cookie wird ungültig
    assert c2.post("/api/auth/logout-all").status_code == 204
    assert c1.get("/api/auth/me").status_code == 401
