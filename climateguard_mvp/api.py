"""ClimateGuard prototype API. Run with: uvicorn api:app --reload"""
from __future__ import annotations

import hashlib
import hmac
import csv
import io
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import requests
from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, field_validator

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("CLIMATEGUARD_DB", str(ROOT / "data" / "climateguard.sqlite3")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
API_PREFIX = "/api/v1"


@contextmanager
def connect():
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS locations (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, admin_name TEXT, latitude REAL, longitude REAL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS kits (
          id TEXT PRIMARY KEY, location_id TEXT NOT NULL REFERENCES locations(id), token_hash TEXT NOT NULL,
          active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS readings (
          id INTEGER PRIMARY KEY AUTOINCREMENT, kit_id TEXT NOT NULL REFERENCES kits(id),
          message_id TEXT NOT NULL, observed_at TEXT NOT NULL, received_at TEXT NOT NULL,
          measurements TEXT NOT NULL, quality TEXT, UNIQUE(kit_id, message_id)
        );
        CREATE TABLE IF NOT EXISTS source_jobs (
          id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, status TEXT NOT NULL,
          started_at TEXT NOT NULL, completed_at TEXT, detail TEXT
        );
        CREATE TABLE IF NOT EXISTS weather (
          location_id TEXT NOT NULL REFERENCES locations(id), observed_on TEXT NOT NULL,
          precipitation_mm REAL, temperature_c REAL, temperature_max_c REAL, temperature_min_c REAL,
          humidity_pct REAL, source TEXT NOT NULL, retrieved_at TEXT NOT NULL,
          PRIMARY KEY(location_id, observed_on, source)
        );
        CREATE TABLE IF NOT EXISTS disease_observations (
          id INTEGER PRIMARY KEY AUTOINCREMENT, location_id TEXT NOT NULL REFERENCES locations(id),
          interval_start TEXT NOT NULL, interval_end TEXT NOT NULL, cases REAL NOT NULL,
          source_key TEXT NOT NULL, source_name TEXT, case_definition TEXT, source_location TEXT,
          quality TEXT, ingested_at TEXT NOT NULL,
          UNIQUE(location_id,interval_start,interval_end,source_key)
        );
        """)


init_db()
app = FastAPI(title="ClimateGuard API", version="0.1.0", description="Environmental and epidemiological early-warning prototype API. Predictions are withheld until validated training data are available.")
origins = [x.strip() for x in os.getenv("CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",") if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type", "Idempotency-Key"])


class Measurement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: float
    unit: str = Field(min_length=1, max_length=24)


class ReadingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kit_id: str = Field(min_length=1, max_length=80)
    timestamp: datetime
    message_id: str = Field(min_length=1, max_length=120)
    temperature_c: Measurement | None = None
    humidity_pct: Measurement | None = None
    rainfall_mm: Measurement | None = None
    water_level_m: Measurement | None = None
    battery_pct: Measurement | None = None
    quality: str | None = Field(default=None, max_length=200)

    @field_validator("timestamp")
    @classmethod
    def timestamp_not_future(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must include a timezone")
        if value > datetime.now(timezone.utc):
            raise ValueError("timestamp cannot be in the future")
        return value.astimezone(timezone.utc)

    @field_validator("temperature_c")
    @classmethod
    def temp_bounds(cls, value):
        if value and (value.unit != "C" or not -60 <= value.value <= 80): raise ValueError("temperature must be -60..80 C")
        return value

    @field_validator("humidity_pct")
    @classmethod
    def humidity_bounds(cls, value):
        if value and (value.unit != "%" or not 0 <= value.value <= 100): raise ValueError("humidity must be 0..100 %")
        return value

    @field_validator("rainfall_mm")
    @classmethod
    def rain_bounds(cls, value):
        if value and (value.unit != "mm" or not 0 <= value.value <= 2000): raise ValueError("rainfall must be 0..2000 mm")
        return value

    @field_validator("water_level_m")
    @classmethod
    def water_bounds(cls, value):
        if value and (value.unit != "m" or not 0 <= value.value <= 1000): raise ValueError("water level must be 0..1000 m")
        return value

    @field_validator("battery_pct")
    @classmethod
    def battery_bounds(cls, value):
        if value and (value.unit != "%" or not 0 <= value.value <= 100): raise ValueError("battery must be 0..100 %")
        return value


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def kit_auth(authorization: str | None = Header(default=None)) -> tuple[str, sqlite3.Row]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer device credential required")
    token = authorization[7:]
    with connect() as db:
        rows = db.execute("SELECT * FROM kits WHERE active=1").fetchall()
    digest = hashlib.sha256(token.encode()).hexdigest()
    kit = next((r for r in rows if hmac.compare_digest(r["token_hash"], digest)), None)
    if kit is None: raise HTTPException(401, "Invalid or revoked device credential")
    return token, kit


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    configured = os.getenv("ADMIN_TOKEN")
    if not configured or not x_admin_token or not hmac.compare_digest(configured, x_admin_token):
        raise HTTPException(403, "Admin access is not configured")


class LocationIn(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    admin_name: str | None = None
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class KitIn(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    location_id: str
    credential: str = Field(min_length=24, max_length=200)


@app.get("/health")
@app.get(f"{API_PREFIX}/health")
def health():
    with connect() as db: db.execute("SELECT 1")
    return {"status": "ok", "database": "ok", "predictions": "unavailable_without_validated_model", "time": utcnow()}


@app.get(f"{API_PREFIX}/locations")
def locations():
    with connect() as db: rows = db.execute("SELECT id,name,admin_name,latitude,longitude FROM locations ORDER BY name").fetchall()
    return {"items": [dict(r) for r in rows]}


@app.post(f"{API_PREFIX}/admin/locations", dependencies=[Depends(require_admin)], status_code=201)
def create_location(item: LocationIn):
    try:
        with connect() as db:
            db.execute("INSERT INTO locations(id,name,admin_name,latitude,longitude,created_at) VALUES(?,?,?,?,?,?)", (item.id,item.name,item.admin_name,item.latitude,item.longitude,utcnow()))
    except sqlite3.IntegrityError: raise HTTPException(409, "Location ID already exists")
    return {"status": "created", "location_id": item.id}


@app.post(f"{API_PREFIX}/admin/kits", dependencies=[Depends(require_admin)], status_code=201)
def create_kit(item: KitIn):
    try:
        with connect() as db:
            db.execute("INSERT INTO kits(id,location_id,token_hash,created_at) VALUES(?,?,?,?)", (item.id,item.location_id,hashlib.sha256(item.credential.encode()).hexdigest(),utcnow()))
    except sqlite3.IntegrityError: raise HTTPException(409, "Kit ID already exists or location is unknown")
    return {"status": "created", "kit_id": item.id, "note": "Credential is not returned by this API; store it securely at provisioning."}


@app.post(f"{API_PREFIX}/admin/kits/{{kit_id}}/revoke", dependencies=[Depends(require_admin)])
def revoke_kit(kit_id: str):
    with connect() as db:
        result = db.execute("UPDATE kits SET active=0 WHERE id=? AND active=1", (kit_id,))
        exists = db.execute("SELECT id,active FROM kits WHERE id=?", (kit_id,)).fetchone()
    if not exists: raise HTTPException(404, "Unknown kit")
    return {"kit_id": kit_id, "status": "revoked" if result.rowcount else "already_revoked"}


@app.get(f"{API_PREFIX}/dashboard/summary")
def summary(location_id: str | None = None):
    with connect() as db:
        if location_id:
            loc = db.execute("SELECT * FROM locations WHERE id=?", (location_id,)).fetchone()
            if not loc: raise HTTPException(404, "Unknown location")
            kit_count = db.execute("SELECT count(*) FROM kits WHERE location_id=? AND active=1", (location_id,)).fetchone()[0]
            recent = db.execute("SELECT observed_at,received_at FROM readings r JOIN kits k ON r.kit_id=k.id WHERE k.location_id=? ORDER BY received_at DESC LIMIT 1", (location_id,)).fetchone()
            case_count = db.execute("SELECT count(*) FROM disease_observations WHERE location_id=?", (location_id,)).fetchone()[0]
            case_latest = db.execute("SELECT max(interval_end) FROM disease_observations WHERE location_id=?", (location_id,)).fetchone()[0]
        else:
            loc, kit_count = None, 0
            recent = db.execute("SELECT observed_at,received_at FROM readings ORDER BY received_at DESC LIMIT 1").fetchone()
            case_count = db.execute("SELECT count(*) FROM disease_observations").fetchone()[0]
            case_latest = db.execute("SELECT max(interval_end) FROM disease_observations").fetchone()[0]
    return {"location": dict(loc) if loc else None, "observations": {"disease_cases": "available" if case_count else "unavailable", "disease_record_count": case_count, "latest_case_interval_end": case_latest, "weather": "unavailable_until_ingested", "sensor_last_observed_at": recent["observed_at"] if recent else None, "sensor_last_received_at": recent["received_at"] if recent else None}, "active_kits": kit_count, "prediction": {"status": "unavailable", "reason": "No validated model and compatible surveillance training data are configured."}, "as_of": utcnow()}


@app.get(f"{API_PREFIX}/dashboard/timeseries")
def timeseries(location_id: str, start: date | None = None, end: date | None = None, limit: int = Query(500, ge=1, le=2000)):
    where, args = ["w.location_id=?"], [location_id]
    if start: where.append("w.observed_on>=?"); args.append(start.isoformat())
    if end: where.append("w.observed_on<=?"); args.append(end.isoformat())
    with connect() as db:
        loc = db.execute("SELECT id FROM locations WHERE id=?", (location_id,)).fetchone()
        if not loc: raise HTTPException(404, "Unknown location")
        rows = db.execute(f"SELECT * FROM weather w WHERE {' AND '.join(where)} ORDER BY observed_on DESC LIMIT ?", (*args, limit)).fetchall()
    return {"items": [dict(r) for r in reversed(rows)], "limit": limit}


@app.get(f"{API_PREFIX}/locations/{{location_id}}/observations")
def disease_observations(location_id: str, limit: int = Query(500, ge=1, le=2000)):
    with connect() as db:
        exists = db.execute("SELECT 1 FROM locations WHERE id=?", (location_id,)).fetchone()
        if not exists: raise HTTPException(404, "Unknown location")
        rows = db.execute("SELECT interval_start,interval_end,cases,source_key,source_name,case_definition,source_location,quality,ingested_at FROM disease_observations WHERE location_id=? ORDER BY interval_start DESC LIMIT ?", (location_id,limit)).fetchall()
    return {"items": [dict(r) for r in reversed(rows)], "note": "Intervals retain source reporting cadence. Overlapping intervals are not summed."}


def parse_opendengue_csv(text: str, source_key: str) -> tuple[list[dict[str, Any]], list[str]]:
    reader = csv.DictReader(io.StringIO(text))
    required = {"location_id", "calendar_start_date", "calendar_end_date", "dengue_total"}
    if not reader.fieldnames or not required.issubset({field.strip() for field in reader.fieldnames}):
        raise ValueError("CSV requires location_id, calendar_start_date, calendar_end_date, and dengue_total columns; provide a stable-ID crosswalk explicitly")
    rows, issues = [], []
    for number, raw in enumerate(reader, start=2):
        if number > 100_001:
            issues.append("CSV row limit exceeded (100,000)")
            break
        row = {str(k).strip(): (v or "").strip() for k,v in raw.items() if k}
        try:
            start, end = date.fromisoformat(row["calendar_start_date"]), date.fromisoformat(row["calendar_end_date"])
            cases = float(row["dengue_total"])
            if end < start or cases < 0 or not cases.is_integer(): raise ValueError("invalid date interval or non-integer/negative case count")
            if not row["location_id"]: raise ValueError("location_id is empty")
            rows.append({"location_id":row["location_id"],"interval_start":start.isoformat(),"interval_end":end.isoformat(),"cases":cases,"source_key":source_key,"source_name":row.get("source_name") or "OpenDengue","case_definition":row.get("case_definition") or None,"source_location":row.get("adm_2_name") or row.get("adm_1_name") or row.get("location_name") or None})
        except (ValueError, KeyError) as exc:
            issues.append(f"row {number}: {str(exc)[:160]}")
    return rows, issues


@app.post(f"{API_PREFIX}/admin/opendengue/import", dependencies=[Depends(require_admin)])
async def import_opendengue(source_key: str = Query(min_length=1, max_length=120), file: UploadFile = File(...)):
    payload = await file.read(10_000_001)
    if len(payload) > 10_000_000: raise HTTPException(413, "CSV must be at most 10 MB")
    try: rows, issues = parse_opendengue_csv(payload.decode("utf-8-sig"), source_key)
    except (UnicodeDecodeError, ValueError) as exc: raise HTTPException(422, str(exc))
    stored, duplicates, unknown, overlaps = 0, 0, [], 0
    job_id = None
    with connect() as db:
        job_id = db.execute("INSERT INTO source_jobs(source,status,started_at) VALUES('opendengue','running',?)", (utcnow(),)).lastrowid
    for row in rows:
        with connect() as db:
            if not db.execute("SELECT 1 FROM locations WHERE id=?", (row["location_id"],)).fetchone():
                unknown.append(row["location_id"]); continue
            exact = db.execute("SELECT 1 FROM disease_observations WHERE location_id=? AND source_key=? AND interval_start=? AND interval_end=?", (row["location_id"],source_key,row["interval_start"],row["interval_end"])).fetchone()
            if exact:
                duplicates += 1
                continue
            overlap = db.execute("SELECT count(*) FROM disease_observations WHERE location_id=? AND interval_start<=? AND interval_end>=?", (row["location_id"],row["interval_end"],row["interval_start"])).fetchone()[0]
            if overlap: overlaps += 1
            quality = "overlapping_interval_review_required" if overlap else None
            cursor = db.execute("INSERT OR IGNORE INTO disease_observations(location_id,interval_start,interval_end,cases,source_key,source_name,case_definition,source_location,quality,ingested_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (row["location_id"],row["interval_start"],row["interval_end"],row["cases"],source_key,row["source_name"],row["case_definition"],row["source_location"],quality,utcnow()))
            if cursor.rowcount: stored += 1
            else: duplicates += 1
    status = "success" if not issues and not unknown else ("partial" if stored else "error")
    detail = json.dumps({"stored":stored,"duplicates":duplicates,"unknown_location_ids":sorted(set(unknown)),"overlap_rows":overlaps,"row_issues":issues[:50]})
    with connect() as db: db.execute("UPDATE source_jobs SET status=?,completed_at=?,detail=? WHERE id=?", (status,utcnow(),detail,job_id))
    return {"source":"OpenDengue CSV", "source_key":source_key, "status":status, "stored":stored, "duplicates":duplicates, "unknown_location_ids":sorted(set(unknown)), "overlap_rows_flagged":overlaps, "row_issues":issues[:50]}


@app.get(f"{API_PREFIX}/data-sources/status")
def source_status():
    with connect() as db:
        jobs = db.execute("SELECT source,status,started_at,completed_at,detail FROM source_jobs ORDER BY id DESC").fetchall()
        disease = db.execute("SELECT count(*) AS n,min(interval_start) AS first_date,max(interval_end) AS last_date,count(DISTINCT location_id) AS locations FROM disease_observations").fetchone()
    latest = {}
    for j in jobs:
        if j["source"] not in latest: latest[j["source"]] = dict(j)
    configured = [{"name": "NASA POWER", "type": "daily gridded/reanalysis weather", "status": "configured" if os.getenv("POWER_ENABLED", "false").lower() == "true" else "available_on_demand", "latest_job": latest.get("nasa_power"), "credentials_required": False},
                  {"name": "OpenDengue", "type": "reported dengue surveillance", "status": "loaded" if disease["n"] else "unavailable", "records": disease["n"], "locations": disease["locations"], "coverage_start": disease["first_date"], "coverage_end": disease["last_date"], "latest_job": latest.get("opendengue"), "credentials_required": False, "detail": "Intervals retain source cadence; revisions/overlaps require review. Predictions remain withheld pending model validation."},
                  {"name": "Satellite, population, boundaries, mobility, vector surveillance", "type": "environmental/context", "status": "unavailable_until_configured", "credentials_required": "varies"}]
    return {"items": configured}


@app.get(f"{API_PREFIX}/kits")
def kits():
    with connect() as db:
        rows = db.execute("SELECT k.id,k.location_id,k.active,k.created_at,max(r.received_at) AS last_seen FROM kits k LEFT JOIN readings r ON r.kit_id=k.id GROUP BY k.id ORDER BY k.id").fetchall()
    return {"items": [dict(r) for r in rows]}


@app.get(f"{API_PREFIX}/kits/{{kit_id}}/status")
def kit_status(kit_id: str):
    with connect() as db:
        r = db.execute("SELECT k.id,k.location_id,k.active,k.created_at,max(o.received_at) AS last_seen FROM kits k LEFT JOIN readings o ON o.kit_id=k.id WHERE k.id=? GROUP BY k.id", (kit_id,)).fetchone()
    if not r: raise HTTPException(404, "Unknown kit")
    return dict(r)


@app.get(f"{API_PREFIX}/kits/{{kit_id}}/readings")
def kit_readings(kit_id: str, limit: int = Query(100, ge=1, le=1000)):
    with connect() as db:
        rows = db.execute("SELECT r.id,r.message_id,r.observed_at,r.received_at,r.measurements,r.quality FROM readings r JOIN kits k ON k.id=r.kit_id WHERE k.id=? ORDER BY r.received_at DESC LIMIT ?", (kit_id,limit)).fetchall()
    if not rows:
        with connect() as db: exists = db.execute("SELECT 1 FROM kits WHERE id=?", (kit_id,)).fetchone()
        if not exists: raise HTTPException(404, "Unknown kit")
    return {"items": [{**dict(r), "measurements": json.loads(r["measurements"])} for r in rows]}


@app.post(f"{API_PREFIX}/ingest/sensor-readings", status_code=201)
def ingest_reading(reading: ReadingIn, auth=Depends(kit_auth)):
    _, kit = auth
    if reading.kit_id != kit["id"]: raise HTTPException(403, "Credential is not authorized for this kit")
    measurements = {key: getattr(reading, key).model_dump() for key in ("temperature_c", "humidity_pct", "rainfall_mm", "water_level_m", "battery_pct") if getattr(reading, key) is not None}
    if not measurements: raise HTTPException(422, "At least one measurement is required")
    try:
        with connect() as db:
            inserted = db.execute("INSERT INTO readings(kit_id,message_id,observed_at,received_at,measurements,quality) VALUES(?,?,?,?,?,?)", (kit["id"], reading.message_id, reading.timestamp.isoformat(), utcnow(), json.dumps(measurements), reading.quality))
    except sqlite3.IntegrityError:
        with connect() as db: old = db.execute("SELECT id FROM readings WHERE kit_id=? AND message_id=?", (kit["id"], reading.message_id)).fetchone()
        return {"status": "duplicate", "reading_id": old["id"], "message_id": reading.message_id}
    return {"status": "stored", "reading_id": inserted.lastrowid, "message_id": reading.message_id, "received_at": utcnow()}


@app.get(f"{API_PREFIX}/predictions")
def predictions():
    return {"items": [], "status": "unavailable", "detail": "A validated model cannot be produced without compatible genuine surveillance data."}


def fetch_power_daily(latitude: float, longitude: float, start: date, end: date) -> list[dict[str, Any]]:
    if start > end: raise ValueError("start must not be after end")
    params = {"parameters": "PRECTOTCORR,T2M,T2M_MAX,T2M_MIN,RH2M", "community": "AG", "longitude": longitude, "latitude": latitude, "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"), "format": "JSON", "time-standard": "UTC"}
    response = requests.get("https://power.larc.nasa.gov/api/temporal/daily/point", params=params, timeout=(5, 45))
    response.raise_for_status()
    params_data = response.json()["properties"]["parameter"]
    keys = params_data["T2M"].keys()
    def valid(v): return None if v is None or float(v) <= -900 else float(v)
    return [{"observed_on": datetime.strptime(day, "%Y%m%d").date().isoformat(), "precipitation_mm": valid(params_data["PRECTOTCORR"].get(day)), "temperature_c": valid(params_data["T2M"].get(day)), "temperature_max_c": valid(params_data["T2M_MAX"].get(day)), "temperature_min_c": valid(params_data["T2M_MIN"].get(day)), "humidity_pct": valid(params_data["RH2M"].get(day))} for day in keys]


@app.post(f"{API_PREFIX}/admin/power/{{location_id}}")
def ingest_power(location_id: str, start: date, end: date, x_admin_token: str | None = Header(default=None)):
    require_admin(x_admin_token)
    if start > end or (end - start).days > 366: raise HTTPException(422, "POWER requests must cover a positive interval of at most 367 days")
    with connect() as db: loc = db.execute("SELECT * FROM locations WHERE id=?", (location_id,)).fetchone()
    if not loc or loc["latitude"] is None or loc["longitude"] is None: raise HTTPException(404, "Location with coordinates not found")
    job_id = None
    try:
        with connect() as db:
            c = db.execute("INSERT INTO source_jobs(source,status,started_at) VALUES('nasa_power','running',?)", (utcnow(),)); job_id = c.lastrowid
        rows = fetch_power_daily(loc["latitude"], loc["longitude"], start, end)
        with connect() as db:
            for row in rows:
                db.execute("INSERT OR REPLACE INTO weather(location_id,observed_on,precipitation_mm,temperature_c,temperature_max_c,temperature_min_c,humidity_pct,source,retrieved_at) VALUES(?,?,?,?,?,?,?,?,?)", (location_id,row["observed_on"],row["precipitation_mm"],row["temperature_c"],row["temperature_max_c"],row["temperature_min_c"],row["humidity_pct"],"NASA POWER daily point",utcnow()))
            db.execute("UPDATE source_jobs SET status='success',completed_at=?,detail=? WHERE id=?", (utcnow(), f"Stored {len(rows)} daily rows", job_id))
        return {"source": "NASA POWER", "location_id": location_id, "rows": len(rows), "status": "success"}
    except Exception as exc:
        if job_id:
            with connect() as db: db.execute("UPDATE source_jobs SET status='error',completed_at=?,detail=? WHERE id=?", (utcnow(), str(exc)[:500], job_id))
        raise HTTPException(502, f"NASA POWER ingestion failed: {str(exc)[:200]}")
