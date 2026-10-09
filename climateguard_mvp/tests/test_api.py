from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token-long-enough")
    import api as service
    monkeypatch.setattr(service, "DB_PATH", tmp_path / "test.sqlite3")
    service.init_db()
    return service


def test_health_reports_prediction_unavailable(api):
    response = api.health()
    assert response["status"] == "ok"
    assert response["predictions"] == "unavailable_without_validated_model"


def test_sensor_validation_auth_and_idempotency(api):
    with api.connect() as db:
        db.execute("INSERT INTO locations(id,name,latitude,longitude,created_at) VALUES(?,?,?,?,?)", ("loc-1", "Test", 21, 79, api.utcnow()))
        db.execute("INSERT INTO kits(id,location_id,token_hash,created_at) VALUES(?,?,?,?)", ("kit-1", "loc-1", api.hashlib.sha256(b"kit-secret-token-that-is-long-enough").hexdigest(), api.utcnow()))
    with api.connect() as db: kit = db.execute("SELECT * FROM kits WHERE id='kit-1'").fetchone()
    reading = api.ReadingIn.model_validate({"kit_id":"kit-1","message_id":"m-1","timestamp":datetime.now(timezone.utc).isoformat(),"temperature_c":{"value":28,"unit":"C"}})
    assert api.ingest_reading(reading, auth=("kit-secret-token-that-is-long-enough", kit))["status"] == "stored"
    duplicate = api.ingest_reading(reading, auth=("kit-secret-token-that-is-long-enough", kit))
    assert duplicate["status"] == "duplicate"
    with pytest.raises(ValidationError):
        api.ReadingIn.model_validate({"kit_id":"kit-1","message_id":"m-2","timestamp":datetime.now(timezone.utc).isoformat(),"humidity_pct":{"value":101,"unit":"%"}})
    item = api.kit_readings("kit-1", limit=100)["items"][0]
    assert item["measurements"]["temperature_c"]["value"] == 28
    assert api.kit_auth("Bearer kit-secret-token-that-is-long-enough")[1]["id"] == "kit-1"
    api.revoke_kit("kit-1")
    with pytest.raises(api.HTTPException): api.kit_auth("Bearer kit-secret-token-that-is-long-enough")


def test_power_adapter_normalizes_sentinel_and_units(api, monkeypatch):
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {"properties":{"parameter":{
                "T2M":{"20260101":22.5},"PRECTOTCORR":{"20260101":1.2},
                "T2M_MAX":{"20260101":-999},"T2M_MIN":{"20260101":18},"RH2M":{"20260101":70}
            }}}
    monkeypatch.setattr(api.requests, "get", lambda *args, **kwargs: Response())
    rows = api.fetch_power_daily(21,79,date(2026,1,1),date(2026,1,1))
    assert rows == [{"observed_on":"2026-01-01","precipitation_mm":1.2,"temperature_c":22.5,"temperature_max_c":None,"temperature_min_c":18.0,"humidity_pct":70.0}]


def test_disease_csv_keeps_monthly_interval_and_rejects_bad_rows(api):
    rows, issues = api.parse_opendengue_csv("location_id,calendar_start_date,calendar_end_date,dengue_total,adm_2_name\nloc-1,2025-01-01,2025-01-31,17,Example District\nloc-1,2025-02-01,2025-02-28,-2,Example District\n", "extract-1")
    assert rows[0]["interval_start"] == "2025-01-01"
    assert rows[0]["interval_end"] == "2025-01-31"
    assert rows[0]["cases"] == 17
    assert rows[0]["source_location"] == "Example District"
    assert len(issues) == 1
