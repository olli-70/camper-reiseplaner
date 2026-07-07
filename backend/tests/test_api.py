import os
import tempfile

# eigene DB-Datei pro Testlauf, bevor die App-Module importiert werden
_fd, _path = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["CAMPER_DB"] = _path

from fastapi.testclient import TestClient  # noqa: E402

from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402

init_db()
client = TestClient(app)


def test_health():
    assert client.get("/api/health").json() == {"status": "ok"}


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
    assert after > before  # Reise wird als 'zuletzt beplant' markiert
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
    # persistiert (list_stops ordert nach reihenfolge)
    got = client.get(f"/api/trips/{tid}/stops").json()
    assert [s["id"] for s in got] == new_order
    client.delete(f"/api/trips/{tid}")


def test_trip_and_stop_lifecycle():
    # Trip anlegen
    r = client.post("/api/trips", json={"name": "Norwegen 2026"})
    assert r.status_code == 201
    trip_id = r.json()["id"]

    # Stopp anlegen
    r = client.post(
        f"/api/trips/{trip_id}/stops",
        json={"name": "Preikestolen", "lat": 58.98, "lng": 6.19, "status": "geplant"},
    )
    assert r.status_code == 201
    stop_id = r.json()["id"]

    # Status aktualisieren
    r = client.patch(f"/api/stops/{stop_id}", json={"status": "besucht"})
    assert r.status_code == 200
    assert r.json()["status"] == "besucht"

    # Reservierung setzen (Checkbox + von/bis mit Datum+Uhrzeit)
    r = client.patch(
        f"/api/stops/{stop_id}",
        json={
            "reserviert": True,
            "reserviert_von": "2026-07-10T14:00",
            "reserviert_bis": "2026-07-12T11:00",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["reserviert"] is True
    assert body["reserviert_von"].startswith("2026-07-10T14:00")
    assert body["reserviert_bis"].startswith("2026-07-12T11:00")

    # ungültiger Status -> 422
    assert client.patch(f"/api/stops/{stop_id}", json={"status": "quatsch"}).status_code == 422

    # Liste enthält den Stopp
    stops = client.get(f"/api/trips/{trip_id}/stops").json()
    assert len(stops) == 1

    # Löschen
    assert client.delete(f"/api/stops/{stop_id}").status_code == 204
    assert client.delete(f"/api/trips/{trip_id}").status_code == 204
