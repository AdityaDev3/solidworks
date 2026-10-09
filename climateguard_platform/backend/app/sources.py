from __future__ import annotations

import csv
import io
import json
import hashlib
import os
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from pyproj import Transformer
from shapely.geometry import shape, mapping
from shapely.ops import transform

from .db import db
from .db import BASE_DIR

POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
GEOb_URL = "https://www.geoboundaries.org/api/current/gbOpen/{country}/{level}/"
WORLDPOP_URL = "https://api.worldpop.org/v2"
OPENDENGUE_DEFAULT = "https://github.com/OpenDengue/master-repo/raw/main/data/releases/V1.3/Spatial_extract_V1_3.zip"
NOW = lambda: datetime.now(timezone.utc).isoformat()
EQUAL_AREA = Transformer.from_crs("EPSG:4326", "EPSG:6933", always_xy=True).transform


def session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=4, backoff_factor=0.7, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET", "POST"))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update({"User-Agent": "ClimateGuardPrototype/1.0 (open data integration)"})
    return s


def _power_chunk(lat: float, lon: float, start: date, end: date) -> list[dict[str, Any]]:
    params = {
        "parameters": "PRECTOTCORR,T2M,T2M_MAX,T2M_MIN,RH2M",
        "community": "AG", "longitude": lon, "latitude": lat,
        "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"),
        "format": "JSON", "time-standard": "UTC",
    }
    cache_dir=BASE_DIR / "data" / "cache" / "nasa_power";cache_dir.mkdir(parents=True,exist_ok=True)
    key=hashlib.sha256(json.dumps(params,sort_keys=True).encode()).hexdigest()
    cache_file=cache_dir / f"{key}.json"
    if cache_file.exists():
        payload=json.loads(cache_file.read_text(encoding="utf-8"))
    else:
        response = session().get(POWER_URL, params=params, timeout=int(os.getenv("NASA_POWER_TIMEOUT_SECONDS", "60")))
        response.raise_for_status()
        payload = response.json()
        cache_file.write_text(json.dumps(payload),encoding="utf-8")
    parameters = payload["properties"]["parameter"]
    def clean(key, day):
        value = parameters[key].get(day)
        return None if value is None or float(value) <= -900 else float(value)
    return [{"date": datetime.strptime(day, "%Y%m%d").date().isoformat(),
             "rain_mm": clean("PRECTOTCORR", day), "temp_c": clean("T2M", day),
             "temp_max_c": clean("T2M_MAX", day), "temp_min_c": clean("T2M_MIN", day),
             "humidity_pct": clean("RH2M", day)} for day in parameters["T2M"]]


def fetch_power(lat: float, lon: float, start: date, end: date) -> list[dict[str, Any]]:
    if start > end or (end - start).days > 1461:
        raise ValueError("NASA POWER range must be positive and no longer than four years")
    out, cursor = [], start
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=364))
        out.extend(_power_chunk(lat, lon, cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return out


def import_power(location_id: str, rows: list[dict[str, Any]], latitude: float, longitude: float) -> int:
    count = 0
    with db() as cx:
        for row in rows:
            values = [("rainfall", row["rain_mm"], "mm/day"), ("temperature_mean", row["temp_c"], "C"),
                      ("temperature_max", row["temp_max_c"], "C"), ("temperature_min", row["temp_min_c"], "C"),
                      ("humidity", row["humidity_pct"], "%")]
            for category, value, unit in values:
                if value is None: continue
                cur = cx.execute("""INSERT OR IGNORE INTO observations
                    (location_id,category,observed_on,observed_to,value,unit,source,product,resolution,quality,metadata,retrieved_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (location_id,category,row["date"],row["date"],value,unit,"NASA POWER","Daily point (MERRA-2/POWER)",
                     "0.5 x 0.625 degree source grid","gridded_reanalysis",json.dumps({"request_latitude":latitude,"request_longitude":longitude}),NOW()))
                count += cur.rowcount
    return count


def fetch_boundaries(country: str = "IND", level: str = "ADM2") -> dict[str, Any]:
    if level not in {"ADM1", "ADM2"}: raise ValueError("Only ADM1 and ADM2 are supported")
    response = session().get(GEOb_URL.format(country=country.upper(), level=level), timeout=45)
    response.raise_for_status()
    meta = response.json()
    url = meta.get("simplifiedGeometryGeoJSON") or meta["gjDownloadURL"]
    geo = session().get(url, timeout=120)
    geo.raise_for_status()
    return {"metadata": meta, "geojson": geo.json()}


def save_boundaries(country: str, level: str, package: dict[str, Any]) -> int:
    meta, collection = package["metadata"], package["geojson"]
    count = 0
    for feature in collection.get("features", []):
        properties = feature.get("properties") or {}
        geometry = feature.get("geometry")
        if not geometry: continue
        geom = shape(geometry)
        if geom.is_empty or not geom.is_valid: geom = geom.buffer(0)
        if geom.is_empty: continue
        centroid = geom.centroid
        area_km2 = transform(EQUAL_AREA, geom).area / 1_000_000
        boundary_id = str(properties.get("shapeID") or properties.get("shapeISO") or properties.get("shapeName"))
        display = str(properties.get("shapeName") or properties.get("shapeGroup") or boundary_id)
        admin1 = str(properties.get("shapeGroup") or "")
        geo_id = f"{country.upper()}-{level}-{boundary_id}"
        simplified = geom.simplify(0.005, preserve_topology=True)
        with db() as cx:
            cx.execute("""INSERT INTO locations(id,name,admin1,country,latitude,longitude,area_sq_km,boundary_id,boundary_year,geometry,geometry_simplified,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,admin1=excluded.admin1,
                latitude=excluded.latitude,longitude=excluded.longitude,area_sq_km=excluded.area_sq_km,boundary_year=excluded.boundary_year,
                geometry=excluded.geometry,geometry_simplified=excluded.geometry_simplified""",
                (geo_id,display,admin1,country.upper(),centroid.y,centroid.x,area_km2,boundary_id,
                 str(meta.get("boundaryYearRepresented") or "unknown"),json.dumps(feature),json.dumps({"type":"Feature","properties":properties,"geometry":mapping(simplified)}),NOW()))
        count += 1
    return count


def worldpop_total(geojson_geometry: dict[str, Any], year: int, api_key: str | None = None) -> float:
    headers = {"X-API-Key": api_key} if api_key else {}
    response = session().post(f"{WORLDPOP_URL}/population", json={"geojson":geojson_geometry,"year":year,"resolution":"1km"}, headers=headers, timeout=60)
    response.raise_for_status()
    task = response.json()["task_id"]
    for _ in range(60):
        status = session().get(f"{WORLDPOP_URL}/tasks/{task}", headers=headers, timeout=30)
        status.raise_for_status()
        result = status.json()
        if result.get("status") == "success":
            count = result.get("result", {}).get("total_population")
            if count is None: raise ValueError("WorldPop result omitted total_population")
            return float(count)
        if result.get("status") == "failure": raise RuntimeError(result.get("error", "WorldPop task failed"))
        time.sleep(2)
    raise TimeoutError("WorldPop task did not finish within 120 seconds")


def save_population(location_id: str, year: int, total: float) -> int:
    with db() as cx:
        loc = cx.execute("SELECT area_sq_km FROM locations WHERE id=?", (location_id,)).fetchone()
        if not loc: raise KeyError(location_id)
        density = total / loc["area_sq_km"] if loc["area_sq_km"] else None
        return cx.execute("""INSERT OR IGNORE INTO observations
            (location_id,category,observed_on,observed_to,value,unit,source,product,resolution,quality,metadata,retrieved_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (location_id,"population_count",f"{year}-01-01",f"{year}-12-31",total,"people","WorldPop API v2",
             "Global2 population estimate", "1km grid summarized to boundary", "annual_estimate",
             json.dumps({"dataset_year":year,"density_people_per_sq_km":density}),NOW())).rowcount


def read_opendengue_csv(payload: bytes) -> list[dict[str, str]]:
    if payload[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            candidates=[info for info in archive.infolist() if info.filename.lower().endswith(".csv") and "source" not in info.filename.lower()]
            csv_names = [info.filename for info in candidates if info.file_size <= 500_000_000]
            if not csv_names: raise ValueError("OpenDengue archive did not contain a case-data CSV")
            # Spatial extract is intended for admin-level/location analysis.
            preferred = next((n for n in csv_names if "spatial" in n.lower()), csv_names[0])
            payload = archive.read(preferred)
    text = payload.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames: raise ValueError("OpenDengue CSV has no header")
    return [{str(k).strip(): (v or "").strip() for k,v in row.items() if k} for row in reader]


def load_exact_crosswalk(path: str) -> dict[tuple[str, str, str], str]:
    with open(path, encoding="utf-8-sig", newline="") as handle:
        rows = csv.DictReader(handle)
        needed = {"adm_0_name","adm_1_name","adm_2_name","location_id"}
        if not rows.fieldnames or not needed.issubset(set(rows.fieldnames)):
            raise ValueError("Crosswalk must have adm_0_name, adm_1_name, adm_2_name, location_id")
        return {(r["adm_0_name"].strip().casefold(),r["adm_1_name"].strip().casefold(),r["adm_2_name"].strip().casefold()):r["location_id"].strip() for r in rows}


def normalize_disease_rows(rows: list[dict[str, str]], crosswalk: dict[tuple[str,str,str],str], country_name="India"):
    normalized, rejected, unmatched = [], [], 0
    required = {"calendar_start_date","calendar_end_date","dengue_total"}
    for line, row in enumerate(rows, start=2):
        if not required.issubset(row):
            raise ValueError("OpenDengue data is missing calendar_start_date, calendar_end_date, or dengue_total")
        country = (row.get("adm_0_name") or "").strip()
        if country.casefold() != country_name.casefold(): continue
        key = (country.casefold(),(row.get("adm_1_name") or "").strip().casefold(),(row.get("adm_2_name") or "").strip().casefold())
        location = crosswalk.get(key)
        if not location:
            unmatched += 1
            continue
        try:
            begin, finish = date.fromisoformat(row["calendar_start_date"]), date.fromisoformat(row["calendar_end_date"])
            cases = float(row["dengue_total"])
            if finish < begin or cases < 0: raise ValueError("invalid interval or case count")
        except (ValueError, TypeError):
            rejected.append(line)
            continue
        interval_days = (finish - begin).days + 1
        normalized.append({"location_id":location,"start":begin.isoformat(),"end":finish.isoformat(),"cases":cases,
                           "metadata":{"source_record":row,"interval_days":interval_days,
                                       "case_definition":row.get("case_definition_original") or row.get("case_definition")}})
    return normalized, {"unmatched_rows":unmatched,"rejected_rows":rejected[:100]}
