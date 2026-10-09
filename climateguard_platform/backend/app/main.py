from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .db import BASE_DIR, db, initialize
from .model import build_weekly_records, global_feature_importance, latest_model, latest_risk, train_model
from .ollama import OLLAMA_MODEL, explain_risk, status as ollama_status
from .sources import (NOW, OPENDENGUE_DEFAULT, fetch_boundaries, fetch_power,
                      import_power, load_exact_crosswalk, normalize_disease_rows,
                      read_opendengue_csv, save_boundaries, save_population, worldpop_total)

initialize()
app = FastAPI(title="ClimateGuard Data & Risk API", version="2.0.0",
              description="Regional climate-health prototype. Model estimates are not public-health declarations or medical advice.")
origins = [v.strip() for v in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if v.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET","POST"],
                   allow_headers=["Authorization","Content-Type","X-Admin-Token","X-WorldPop-API-Key"])
CATEGORIES = {"rainfall","temperature_mean","temperature_max","temperature_min","humidity","dengue_cases",
              "population_count","ndvi","evi","land_surface_temperature","surface_water_occurrence",
              "water_level","mobility_index","vector_count","vector_larval_index","land_cover"}
NOW_DT = lambda: datetime.now(timezone.utc)
CROSSWALK = Path(os.getenv("OPENDENGUE_CROSSWALK", str(BASE_DIR / "data" / "opendengue_crosswalk.csv")))


def admin_auth(x_admin_token: str | None = Header(default=None)) -> None:
    expected = os.getenv("ADMIN_TOKEN", "")
    if not expected or not x_admin_token or not hmac.compare_digest(expected, x_admin_token):
        raise HTTPException(403, "Admin key is missing or invalid; set ADMIN_TOKEN in backend/.env")


def start_job(source: str) -> int:
    with db() as cx:
        return cx.execute("INSERT INTO source_jobs(source,status,started_at) VALUES(?,?,?)", (source,"running",NOW())).lastrowid


def finish_job(job_id: int, status: str, records: int, detail: dict):
    with db() as cx:
        cx.execute("UPDATE source_jobs SET status=?,finished_at=?,records=?,detail=? WHERE id=?",
                   (status,NOW(),records,json.dumps(detail)[:6000],job_id))


def insert_observation(location_id: str, category: str, start: str, end: str | None, value: float,
                       unit: str, source: str, product: str | None, resolution: str | None,
                       quality: str | None = None, metadata: dict | None = None) -> int:
    with db() as cx:
        cur = cx.execute("""INSERT OR IGNORE INTO observations
            (location_id,category,observed_on,observed_to,value,unit,source,product,resolution,quality,metadata,retrieved_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (location_id,category,start,end,value,unit,source,product,resolution,quality,json.dumps(metadata or {}),NOW()))
        return cur.rowcount


def source_catalog() -> list[dict]:
    with db() as cx:
        coverage = cx.execute("SELECT category,source,count(*) n,max(observed_to) latest FROM observations GROUP BY category,source").fetchall()
        jobs = cx.execute("SELECT source,status,started_at,finished_at,records,detail FROM source_jobs ORDER BY id DESC").fetchall()
        boundary_count = cx.execute("SELECT count(*) FROM locations WHERE boundary_id IS NOT NULL").fetchone()[0]
    by_source = {}
    for row in jobs:
        if row["source"] not in by_source: by_source[row["source"]] = dict(row)
    cov = {}
    for row in coverage:
        cov.setdefault(row["source"], {"records":0,"latest_observation":None,"categories":[]})
        cov[row["source"]]["records"] += row["n"]
        cov[row["source"]]["latest_observation"] = max(filter(None,[cov[row["source"]]["latest_observation"],row["latest"]]),default=None)
        cov[row["source"]]["categories"].append(row["category"])
    return [
        {"id":"boundaries","name":"geoBoundaries India ADM2","category":"Geography","status":"healthy" if boundary_count else "not_loaded","coverage":{"records":boundary_count,"latest_observation":None,"categories":["district boundaries"] if boundary_count else []},"last_job":by_source.get("geoBoundaries"),"update_note":"Boundary vintage is stored per district; CC BY attribution required."},
        {"id":"weather","name":"NASA POWER daily climate","category":"Weather & climate","status":"healthy" if cov.get("NASA POWER",{}).get("records") else "not_loaded","coverage":cov.get("NASA POWER",{}),"last_job":by_source.get("NASA POWER"),"update_note":"Gridded/reanalysis estimates, not local live weather or forecasts."},
        {"id":"dengue","name":"OpenDengue reported cases","category":"Disease surveillance","status":"healthy" if cov.get("OpenDengue",{}).get("records") else ("needs_crosswalk" if not CROSSWALK.exists() else "not_loaded"),"coverage":cov.get("OpenDengue",{}),"last_job":by_source.get("OpenDengue"),"update_note":"Exact district crosswalk required. Source intervals and overlaps are retained; reports may be delayed/revised."},
        {"id":"population","name":"WorldPop population estimates","category":"Population density","status":"healthy" if cov.get("WorldPop",{}).get("records") else "available_on_request","coverage":cov.get("WorldPop",{}),"last_job":by_source.get("WorldPop"),"update_note":"Annual static estimates; year and resolution are stored."},
        {"id":"satellite","name":"NASA MODIS vegetation / LST","category":"Satellite environment","status":"healthy" if any(cov.get(s,{}).get("records") for s in ["MODIS","NASA Earthdata"]) else ("credential_required" if not os.getenv("EARTHDATA_TOKEN") else "not_loaded"),"coverage":cov.get("MODIS",{}),"last_job":by_source.get("MODIS"),"update_note":"Use quality-screened MOD13Q1/MYD13Q1 16-day composites. Earthdata processing/export must be configured; not a live sensor."},
        {"id":"water","name":"JRC Global Surface Water","category":"Surface water","status":"historical_product_outside_window","coverage":cov.get("JRC Global Surface Water",{}),"last_job":by_source.get("JRC Global Surface Water"),"update_note":"Current Global Surface Water v1.4 history ends in 2021; it cannot fill a 2022–2026 series. It is not a mosquito-breeding measurement."},
        {"id":"mobility","name":"Aggregated human mobility","category":"Mobility","status":"data_loaded" if any("mobility_index" in item.get("categories",[]) for item in cov.values()) else "unavailable","coverage":next((item for item in cov.values() if "mobility_index" in item.get("categories",[])),cov.get("mobility",{})),"last_job":by_source.get("mobility"),"update_note":"No default mobility source is configured. Imported values must be aggregated, privacy-preserving and legally authorized; no proxy is presented as observed."},
        {"id":"vector","name":"Vector surveillance / local kits","category":"Vector & site indicators","status":"healthy" if cov.get("Local IoT kit",{}).get("records") and "vector_count" in cov.get("Local IoT kit",{}).get("categories",[]) else "no_validated_vector_feed","coverage":cov.get("Local IoT kit",{}),"last_job":by_source.get("Vector surveillance"),"update_note":"Only explicitly measured trap/count records are vector signals; weather is never converted into mosquito abundance."},
    ]


class Reading(BaseModel):
    value: float
    unit: str = Field(min_length=1,max_length=24)


class SensorIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kit_id: str = Field(min_length=1,max_length=80)
    message_id: str = Field(min_length=1,max_length=120)
    timestamp: datetime
    temperature_c: Reading | None = None
    humidity_pct: Reading | None = None
    rainfall_mm: Reading | None = None
    water_level_m: Reading | None = None
    vector_count: Reading | None = None
    battery_pct: Reading | None = None
    quality: str | None = Field(default=None,max_length=200)

    @field_validator("timestamp")
    @classmethod
    def aware_not_future(cls, value):
        if value.tzinfo is None: raise ValueError("timestamp must include timezone")
        if value > NOW_DT(): raise ValueError("timestamp cannot be in the future")
        return value.astimezone(timezone.utc)

    @field_validator("temperature_c")
    @classmethod
    def temp_valid(cls,v):
        if v and (v.unit!="C" or not -60<=v.value<=80): raise ValueError("temperature must be Celsius from -60 to 80")
        return v

    @field_validator("humidity_pct","battery_pct")
    @classmethod
    def pct_valid(cls,v):
        if v and (v.unit!="%" or not 0<=v.value<=100): raise ValueError("percentage must be between 0 and 100")
        return v

    @field_validator("rainfall_mm")
    @classmethod
    def rain_valid(cls,v):
        if v and (v.unit!="mm" or not 0<=v.value<=2000): raise ValueError("rainfall must be 0 to 2000 mm")
        return v

    @field_validator("water_level_m")
    @classmethod
    def water_valid(cls,v):
        if v and (v.unit!="m" or not 0<=v.value<=1000): raise ValueError("water level must be 0 to 1000 m")
        return v

    @field_validator("vector_count")
    @classmethod
    def vector_valid(cls,v):
        if v and (v.unit!="count" or not 0<=v.value<=100000): raise ValueError("vector count must be a nonnegative count")
        return v


class KitRegistration(BaseModel):
    kit_id: str = Field(min_length=1,max_length=80)
    location_id: str = Field(min_length=1,max_length=120)
    credential: str = Field(min_length=24,max_length=200)


def device_kit(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "): raise HTTPException(401,"Device bearer credential required")
    token = authorization[7:]
    with db() as cx: rows = cx.execute("SELECT * FROM kits WHERE active=1").fetchall()
    digest = hashlib.sha256(token.encode()).hexdigest()
    kit = next((row for row in rows if hmac.compare_digest(row["token_hash"],digest)),None)
    if not kit: raise HTTPException(401,"Invalid or revoked kit credential")
    return kit


@app.get("/api/v1/health")
def health():
    with db() as cx: cx.execute("SELECT 1")
    return {"status":"ok","database":"ready","ollama":ollama_status(),"predictions":"requires_real_training_data"}


@app.get("/api/v1/locations")
def locations(q: str | None = None, limit: int = Query(1000,ge=1,le=2000)):
    with db() as cx:
        if q:
            rows = cx.execute("SELECT id,name,admin1,country,latitude,longitude,area_sq_km,boundary_year FROM locations WHERE name LIKE ? OR admin1 LIKE ? ORDER BY name LIMIT ?",(f"%{q}%",f"%{q}%",limit)).fetchall()
        else:
            rows = cx.execute("SELECT id,name,admin1,country,latitude,longitude,area_sq_km,boundary_year FROM locations ORDER BY name LIMIT ?",(limit,)).fetchall()
    return {"items":[dict(r) for r in rows]}


@app.get("/api/v1/locations/geojson")
def locations_geojson(limit: int = Query(1200,ge=1,le=2000)):
    with db() as cx: rows=cx.execute("SELECT id,name,admin1,geometry_simplified FROM locations WHERE geometry_simplified IS NOT NULL LIMIT ?",(limit,)).fetchall()
    features=[]
    for r in rows:
        feature=json.loads(r["geometry_simplified"]); feature.setdefault("properties",{}).update({"id":r["id"],"name":r["name"],"admin1":r["admin1"]}); features.append(feature)
    return {"type":"FeatureCollection","features":features}


@app.get("/api/v1/sources")
def sources(): return {"items":source_catalog()}


@app.get("/api/v1/overview")
def overview():
    with db() as cx:
        districts=cx.execute("SELECT count(*) FROM locations").fetchone()[0]
        counts=cx.execute("SELECT category,count(*) n,max(observed_to) latest FROM observations GROUP BY category").fetchall()
        kits=cx.execute("SELECT count(*) FROM kits WHERE active=1").fetchone()[0]
        last_sensor=cx.execute("SELECT max(received_at) FROM sensor_readings").fetchone()[0]
        model=cx.execute("SELECT version,trained_at,metrics FROM model_runs WHERE status='ready' ORDER BY id DESC LIMIT 1").fetchone()
        lastjob=cx.execute("SELECT max(finished_at) FROM source_jobs").fetchone()[0]
    categories={r["category"]:{"records":r["n"],"latest":r["latest"]} for r in counts}
    return {"locations":districts,"observations":sum(v["records"] for v in categories.values()),"categories":categories,
            "active_kits":kits,"last_sensor_received":last_sensor,"last_ingestion":lastjob,
            "model":{"version":model["version"],"trained_at":model["trained_at"],"metrics":json.loads(model["metrics"])} if model else None,
            "generated_at":NOW()}


@app.get("/api/v1/locations/{location_id}/series")
def series(location_id: str, category: str | None = None, start: date | None = None, end: date | None = None, limit: int = Query(5000,ge=1,le=15000)):
    where=["location_id=?"]; args=[location_id]
    if category:
        if category not in CATEGORIES: raise HTTPException(422,"Unknown observation category")
        where.append("category=?"); args.append(category)
    if start: where.append("observed_on>=?"); args.append(start.isoformat())
    if end: where.append("observed_on<=?"); args.append(end.isoformat())
    with db() as cx:
        loc=cx.execute("SELECT id FROM locations WHERE id=?",(location_id,)).fetchone()
        if not loc: raise HTTPException(404,"Unknown location")
        rows=cx.execute(f"SELECT category,observed_on,observed_to,value,unit,source,product,resolution,quality,metadata,retrieved_at FROM observations WHERE {' AND '.join(where)} ORDER BY observed_on LIMIT ?",(*args,limit)).fetchall()
    return {"items":[{**dict(row),"metadata":json.loads(row["metadata"] or "{}" )} for row in rows],"limit":limit}


@app.get("/api/v1/locations/{location_id}/risk")
def location_risk(location_id: str):
    with db() as cx: exists=cx.execute("SELECT 1 FROM locations WHERE id=?",(location_id,)).fetchone()
    if not exists: raise HTTPException(404,"Unknown location")
    result=latest_risk(location_id)
    if result.get("status")=="available":
        try:
            result["narrative"] = explain_risk(result["explanation"])
            result["narrative_source"] = "Ollama" if result["narrative"] else "structured measured features"
        except requests.RequestException:
            result["narrative"] = None; result["narrative_source"] = "structured measured features"
    return result


@app.get("/api/v1/model")
def model_status(location_id: str | None=None):
    artifact=latest_model(location_id)
    if not artifact: return {"status":"unavailable","location_id":location_id,"reason":"No model trained from sufficient genuine weekly surveillance and climate records for this district.","features":[]}
    with db() as cx: run=cx.execute("SELECT trained_at,training_start,training_end FROM model_runs WHERE version=?",(artifact["version"],)).fetchone()
    return {"status":"ready","location_id":artifact["location_id"],"version":artifact["version"],"trained_at":run["trained_at"],"training_start":run["training_start"],"training_end":run["training_end"],"metrics":artifact["metrics"],"features":global_feature_importance(artifact["location_id"]),"target":"at least one of the next four contiguous weekly case counts exceeds the training-only 75th percentile","validation":"location-specific chronological train/calibration/test blocks"}


@app.get("/api/v1/jobs")
def jobs(limit: int=Query(50,ge=1,le=200)):
    with db() as cx: rows=cx.execute("SELECT * FROM source_jobs ORDER BY id DESC LIMIT ?",(limit,)).fetchall()
    return {"items":[{**dict(r),"detail":json.loads(r["detail"] or "{}") } for r in rows]}


@app.get("/api/v1/kits")
def kits():
    with db() as cx: rows=cx.execute("SELECT k.id,k.location_id,k.active,k.created_at,max(s.received_at) last_seen FROM kits k LEFT JOIN sensor_readings s ON s.kit_id=k.id GROUP BY k.id").fetchall()
    return {"items":[dict(r) for r in rows]}


@app.post("/api/v1/ingest/sensor-readings",status_code=201)
def ingest_sensor(reading: SensorIn, kit=Depends(device_kit)):
    if kit["id"] != reading.kit_id: raise HTTPException(403,"Credential does not match kit_id")
    metrics={k:getattr(reading,k).model_dump() for k in ("temperature_c","humidity_pct","rainfall_mm","water_level_m","vector_count","battery_pct") if getattr(reading,k) is not None}
    if not metrics: raise HTTPException(422,"At least one physical measurement is required")
    try:
        with db() as cx:
            cur=cx.execute("INSERT INTO sensor_readings(kit_id,message_id,observed_at,received_at,measurements,quality) VALUES(?,?,?,?,?,?)",(kit["id"],reading.message_id,reading.timestamp.isoformat(),NOW(),json.dumps(metrics),reading.quality))
            kit_location=kit["location_id"]
            for key,val in metrics.items():
                cat={"temperature_c":"temperature_mean","humidity_pct":"humidity","rainfall_mm":"rainfall","water_level_m":"water_level","vector_count":"vector_count"}.get(key)
                if cat: cx.execute("INSERT OR IGNORE INTO observations(location_id,category,observed_on,observed_to,value,unit,source,product,resolution,quality,metadata,retrieved_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(kit_location,cat,reading.timestamp.date().isoformat(),reading.timestamp.date().isoformat(),val["value"],val["unit"],"Local IoT kit",key,"site sensor",reading.quality,json.dumps({"kit_id":kit["id"],"message_id":reading.message_id}),NOW()))
    except Exception as exc:
        if "UNIQUE constraint failed" in str(exc):
            with db() as cx: old=cx.execute("SELECT id FROM sensor_readings WHERE kit_id=? AND message_id=?",(kit["id"],reading.message_id)).fetchone()
            return {"status":"duplicate","reading_id":old["id"]}
        raise
    return {"status":"stored","reading_id":cur.lastrowid,"received_at":NOW()}


@app.post("/api/v1/admin/kits",dependencies=[Depends(admin_auth)],status_code=201)
def register_kit(item: KitRegistration):
    with db() as cx:
        try: cx.execute("INSERT INTO kits(id,location_id,token_hash,created_at) VALUES(?,?,?,?)",(item.kit_id,item.location_id,hashlib.sha256(item.credential.encode()).hexdigest(),NOW()))
        except Exception as exc:
            if "FOREIGN KEY" in str(exc): raise HTTPException(404,"Unknown location")
            if "UNIQUE" in str(exc): raise HTTPException(409,"Kit already exists")
            raise
    return {"kit_id":item.kit_id,"status":"created","note":"Credential is hashed and cannot be retrieved later."}


@app.post("/api/v1/admin/kits/{kit_id}/revoke",dependencies=[Depends(admin_auth)])
def revoke_kit(kit_id: str):
    with db() as cx:
        changed=cx.execute("UPDATE kits SET active=0 WHERE id=? AND active=1",(kit_id,)).rowcount
        exists=cx.execute("SELECT 1 FROM kits WHERE id=?",(kit_id,)).fetchone()
    if not exists: raise HTTPException(404,"Unknown kit")
    return {"kit_id":kit_id,"status":"revoked" if changed else "already_revoked"}


@app.post("/api/v1/admin/ingest/boundaries",dependencies=[Depends(admin_auth)])
def ingest_boundaries(country: str="IND",level: str="ADM2"):
    job=start_job("geoBoundaries")
    try:
        package=fetch_boundaries(country,level); count=save_boundaries(country,level,package)
        finish_job(job,"success",count,{"country":country,"level":level,"boundary_id":package["metadata"].get("boundaryID"),"boundary_year":package["metadata"].get("boundaryYearRepresented"),"license":package["metadata"].get("boundaryLicense"),"attribution":"geoBoundaries / William & Mary GeoLab, CC BY 4.0"})
        return {"status":"success","locations_loaded":count,"metadata":{k:package["metadata"].get(k) for k in ["boundaryID","boundaryYearRepresented","boundaryLicense","boundarySource"]}}
    except Exception as exc:
        finish_job(job,"failed",0,{"error":str(exc)[:500]}); raise HTTPException(502,"Boundary ingestion failed: "+str(exc)[:200])


@app.post("/api/v1/admin/ingest/weather",dependencies=[Depends(admin_auth)])
def ingest_weather(start: date | None=None,end: date | None=None,offset: int=Query(0,ge=0),limit: int=Query(5,ge=1,le=25)):
    end=end or date.today(); start=start or (end-timedelta(days=1460))
    if start>end or (end-start).days>1461: raise HTTPException(422,"Choose a positive historical range of no more than four years")
    with db() as cx: locations=cx.execute("SELECT id,name,latitude,longitude FROM locations WHERE latitude IS NOT NULL AND longitude IS NOT NULL ORDER BY id LIMIT ? OFFSET ?",(limit,offset)).fetchall()
    job=start_job("NASA POWER"); result=[]; failures=[]; added=0
    for loc in locations:
        try:
            rows=fetch_power(loc["latitude"],loc["longitude"],start,end)
            count=import_power(loc["id"],rows,loc["latitude"],loc["longitude"]); added+=count
            result.append({"location_id":loc["id"],"name":loc["name"],"records":len(rows),"new_observations":count})
        except Exception as exc: failures.append({"location_id":loc["id"],"error":str(exc)[:220]})
    status="failed" if failures and not result else "partial" if failures else "success"
    detail={"start":start.isoformat(),"end":end.isoformat(),"locations":result,"failures":failures}
    finish_job(job,status,added,detail)
    return {"status":status,"locations_requested":len(locations),"new_observations":added,"offset":offset,"next_offset":offset+limit if len(locations)==limit else None,"failures":failures}


@app.post("/api/v1/admin/ingest/population",dependencies=[Depends(admin_auth)])
def ingest_population(year: int | None=Query(default=None,ge=2015,le=2030),start_year: int=Query(2022,ge=2015,le=2030),end_year: int=Query(2025,ge=2015,le=2030),offset: int=Query(0,ge=0),limit: int=Query(1,ge=1,le=10),api_key: str | None=Header(default=None,alias="X-WorldPop-API-Key")):
    if year is not None: start_year=end_year=year
    if end_year<start_year or end_year-start_year>3: raise HTTPException(422,"Population request supports up to four annual estimates per run")
    with db() as cx: locations=cx.execute("SELECT id,name,geometry,area_sq_km FROM locations WHERE geometry IS NOT NULL ORDER BY id LIMIT ? OFFSET ?",(limit,offset)).fetchall()
    job=start_job("WorldPop"); success=[]; failures=[]; added=0
    for loc in locations:
        for dataset_year in range(start_year,end_year+1):
            try:
                feature=json.loads(loc["geometry"]); total=worldpop_total(feature["geometry"],dataset_year,api_key)
                count=save_population(loc["id"],dataset_year,total); added+=count
                success.append({"location_id":loc["id"],"year":dataset_year,"population":total,"density_per_sq_km":total/loc["area_sq_km"] if loc["area_sq_km"] else None})
            except Exception as exc: failures.append({"location_id":loc["id"],"year":dataset_year,"error":str(exc)[:200]})
    status="failed" if failures and not success else "partial" if failures else "success"
    finish_job(job,status,added,{"dataset_years":[start_year,end_year],"resolution":"1km","success":success,"failures":failures})
    return {"status":status,"dataset_years":[start_year,end_year],"populations":success,"failures":failures,"next_offset":offset+limit if len(locations)==limit else None}


@app.post("/api/v1/admin/opendengue/crosswalk",dependencies=[Depends(admin_auth)])
async def upload_crosswalk(file: UploadFile=File(...)):
    payload=await file.read(2_000_001)
    if len(payload)>2_000_000: raise HTTPException(413,"Crosswalk must be under 2MB")
    path=CROSSWALK; path.parent.mkdir(parents=True,exist_ok=True)
    try:
        temp=path.with_suffix(path.suffix+".tmp"); temp.write_bytes(payload)
        load_exact_crosswalk(str(temp)); temp.replace(path)
    except Exception as exc:
        if path.with_suffix(path.suffix+".tmp").exists(): path.with_suffix(path.suffix+".tmp").unlink()
        raise HTTPException(422,"Invalid crosswalk: "+str(exc)[:200])
    return {"status":"saved","path":str(path),"rows":sum(1 for _ in csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))}


@app.post("/api/v1/admin/ingest/opendengue",dependencies=[Depends(admin_auth)])
async def ingest_opendengue(file: UploadFile | None=File(default=None),source_url: str | None=None):
    if not CROSSWALK.exists(): raise HTTPException(409,"Upload an explicit district crosswalk first; fuzzy name matching is disabled")
    job=start_job("OpenDengue");
    try:
        if file:
            payload=await file.read(250_000_001)
            if len(payload)>250_000_000: raise HTTPException(413,"OpenDengue file must be at most 250MB")
        else:
            url=source_url or os.getenv("OPENDENGUE_CSV_URL",OPENDENGUE_DEFAULT)
            response=requests.get(url,timeout=(10,180),stream=True); response.raise_for_status()
            blocks=[]; size=0
            for block in response.iter_content(1024*1024):
                size+=len(block)
                if size>250_000_000: raise HTTPException(413,"OpenDengue archive exceeds 250MB")
                blocks.append(block)
            payload=b"".join(blocks)
        rows=read_opendengue_csv(payload)
        crosswalk=load_exact_crosswalk(str(CROSSWALK))
        normalized, quality=normalize_disease_rows(rows,crosswalk)
        inserted=overlaps=0
        for record in normalized:
            with db() as cx:
                exact=cx.execute("SELECT 1 FROM observations WHERE location_id=? AND category='dengue_cases' AND observed_on=? AND observed_to=? AND source='OpenDengue' AND product='V1.3'",(record["location_id"],record["start"],record["end"])).fetchone()
                if exact: continue
                overlap=cx.execute("SELECT 1 FROM observations WHERE location_id=? AND category='dengue_cases' AND observed_on<=? AND observed_to>=? LIMIT 1",(record["location_id"],record["end"],record["start"])).fetchone()
                if overlap: overlaps+=1
                cur=cx.execute("""INSERT OR IGNORE INTO observations(location_id,category,observed_on,observed_to,value,unit,source,product,resolution,quality,metadata,retrieved_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",(record["location_id"],"dengue_cases",record["start"],record["end"],record["cases"],"reported cases","OpenDengue","V1.3","source reporting interval","overlap_requires_review" if overlap else None,json.dumps(record["metadata"]),NOW()))
                inserted+=cur.rowcount
        status="success" if not quality["rejected_rows"] and not quality["unmatched_rows"] else "partial"
        detail={"source_rows":len(rows),"india_rows_matched":len(normalized),"inserted":inserted,"overlap_rows_flagged":overlaps,**quality}
        finish_job(job,status,inserted,detail)
        return {"status":status,**detail}
    except HTTPException as exc:
        finish_job(job,"failed",0,{"error":str(exc.detail)}); raise
    except Exception as exc:
        finish_job(job,"failed",0,{"error":str(exc)[:500]}); raise HTTPException(502,"OpenDengue import failed: "+str(exc)[:200])


class ObservationImport(BaseModel):
    location_id: str
    category: str
    observed_on: date
    observed_to: date | None=None
    value: float
    unit: str
    source: str
    product: str | None=None
    resolution: str | None=None
    quality: str | None=None
    metadata: dict | None=None


@app.post("/api/v1/admin/observations/import",dependencies=[Depends(admin_auth)])
def import_special_observation(item: ObservationImport):
    if item.category not in CATEGORIES: raise HTTPException(422,"Category is not supported")
    if item.category in {"dengue_cases","population_count","rainfall","temperature_mean","humidity"}: raise HTTPException(422,"Use the source-specific importer for this category")
    if item.observed_to and item.observed_to < item.observed_on: raise HTTPException(422,"observed_to precedes observed_on")
    with db() as cx:
        if not cx.execute("SELECT 1 FROM locations WHERE id=?",(item.location_id,)).fetchone(): raise HTTPException(404,"Unknown location")
    created=insert_observation(item.location_id,item.category,item.observed_on.isoformat(),item.observed_to.isoformat() if item.observed_to else item.observed_on.isoformat(),item.value,item.unit,item.source,item.product,item.resolution,item.quality,item.metadata)
    return {"status":"stored" if created else "duplicate"}


@app.post("/api/v1/admin/observations/import-csv",dependencies=[Depends(admin_auth)])
async def import_special_csv(file: UploadFile=File(...)):
    payload=await file.read(25_000_001)
    if len(payload)>25_000_000: raise HTTPException(413,"Processed observation CSV must be at most 25MB")
    try:
        reader=csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
        fields={f.strip() for f in (reader.fieldnames or [])}
        required={"location_id","category","observed_on","value","unit","source"}
        if not required.issubset(fields): raise ValueError("CSV requires columns: location_id, category, observed_on, value, unit, source")
        stored=duplicates=rejected=0; details=[]
        with db() as cx:
            known={row[0] for row in cx.execute("SELECT id FROM locations").fetchall()}
            for line,row in enumerate(reader,start=2):
                try:
                    normalized={str(k).strip():str(v or "").strip() for k,v in row.items() if k}
                    if normalized.get("metadata"): normalized["metadata"]=json.loads(normalized["metadata"])
                    if not normalized.get("observed_to"): normalized["observed_to"]=normalized["observed_on"]
                    item=ObservationImport.model_validate({**normalized,"value":float(normalized["value"])})
                    if item.category not in CATEGORIES: raise ValueError("unknown category")
                    if item.category in {"dengue_cases","population_count","rainfall","temperature_mean","humidity"}: raise ValueError("use the source-specific importer for this category")
                    if item.location_id not in known: raise ValueError("unknown location_id; use a registered boundary ID")
                    cur=cx.execute("""INSERT OR IGNORE INTO observations(location_id,category,observed_on,observed_to,value,unit,source,product,resolution,quality,metadata,retrieved_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",(item.location_id,item.category,item.observed_on.isoformat(),item.observed_to.isoformat() if item.observed_to else item.observed_on.isoformat(),item.value,item.unit,item.source,item.product,item.resolution,item.quality,json.dumps(item.metadata or {}),NOW()))
                    if cur.rowcount: stored+=1
                    else: duplicates+=1
                except Exception as exc:
                    rejected+=1
                    if len(details)<50: details.append({"row":line,"error":str(exc)[:140]})
        return {"status":"partial" if rejected else "success","stored":stored,"duplicates":duplicates,"rejected":rejected,"row_issues":details}
    except UnicodeDecodeError: raise HTTPException(422,"CSV must use UTF-8 encoding")


@app.post("/api/v1/admin/model/train",dependencies=[Depends(admin_auth)])
def train(location_id: str=Query(min_length=1)):
    try: return {"status":"ready",**train_model(location_id)}
    except ValueError as exc: raise HTTPException(422,str(exc))


@app.get("/api/v1/predictions")
def predictions(location_id: str | None=None,limit: int=Query(100,ge=1,le=500)):
    with db() as cx:
        where=" WHERE location_id=?" if location_id else ""; args=[location_id] if location_id else []
        rows=cx.execute(f"SELECT * FROM predictions{where} ORDER BY id DESC LIMIT ?",(*args,limit)).fetchall()
    return {"items":[{**dict(row),"explanation":json.loads(row["explanation"] or "{}" )} for row in rows]}
