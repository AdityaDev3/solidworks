# ClimateGuard project audit

Audit date: 2026-10-09

## Current architecture

The repository contains two distinct prototypes. `climateguard_mvp/app.py` is a Python Streamlit application that accepts a location-specific CSV or creates explicitly synthetic weekly data. It aggregates daily NASA POWER weather to Sunday-ending weeks, then fits a RandomForestClassifier for next-week case activity. It has no persistence or web API. `Frontend/aarogyasight-html-css-js/aarogyasight` is a static HTML/CSS/JavaScript multi-page dashboard with a shared visual system in `styles.css`, no bundler, and no existing API client. These applications are currently disconnected.

There are also local NASA POWER daily CSV downloads, an India population raster, a global population raster, and a problem-statement file. The inspected POWER CSV header covers 2020-01-01 through 2026-08-31 at 18.2754N, 73.501E, with daily precipitation (mm/day), T2M (°C), and RH2M (%); one export omits max/min temperature, so columns differ by extract. This coordinate should not be assumed to represent a district without a documented location crosswalk. Population raster CRS/bounds/nodata have not been verified. No OpenDengue data or administrative boundary dataset is currently present.

## Frontend routes, components, and data expectations

| Page | Existing UI / expected fields | Current behavior |
|---|---|---|
| `index.html` | Summary cards; map; district detail; case/weather charts; sensors; alerts | All values and history are in `app.js` or HTML literals |
| `disease-map.html` | State map and district risk selection | Illustrative state fills; no geographic join |
| `predictions.html` | Forecast chart, risk, factors | Fixed sample values |
| `climate-data.html` | Temperature, rainfall, humidity and history | Static sample values |
| `iot-sensors.html` | Kit readings and status | Static sample values |
| `insights.html` | Environmental context | Static sample values |
| `reports.html` | Risk table, chart and CSV export | Static sample values |
| `about.html` | Product and limitation copy | Static |

The shared frontend has navigation/sidebar, cards, inline SVG charts and map, search, modals, notification list, export action, responsive CSS and theme tokens. Preserve these. Existing integration fields in `app.js`: locations `{name,state,risk,temperature,rain,humidity,change,lng,lat}`, alert `{message,time,tone}`, plus hard-coded historical/prediction arrays. Risk and forecasts must not be populated with guessed values. Risk percentages and alert copy have no defensible source.

## Mock data and incomplete behavior

- `app.js`: `DEMO_DATA`, `ALERTS`, disease adjustment constants, predicted/historical arrays, synthetic map coloring, and report export.
- `index.html` and secondary pages: static illustrative temperatures, water level, AQI, soil moisture, reported cases, sensor statuses, and prediction charts.
- `climateguard_mvp/app.py`: deterministic synthetic data explicitly labeled as demo; user-supplied CSV is the only surveillance path. The in-memory model uses a chronological holdout but lacks persisted artifacts and a seasonal baseline comparison.
- No device enrollment/authentication, API, database, automated job runner, source registry, or frontend loading/error states.

## Integration map and minimum backend

Add a FastAPI service alongside the MVP, with SQLite persistence by default and environment-configured PostgreSQL support as a later deployment option. Keep source adapters independent. Implement health, locations, summary, timeseries, source status, kits/status, and authenticated sensor ingestion. A source registry should expose availability and freshness without implying that imported data are live. Frontend calls the API only when configured/reachable; otherwise retain the page structure and show an explicit synthetic/unavailable notice. Do not turn illustrative values into observations.

The existing MVP remains available for research CSV exploration, but its model output is not a validated operational forecast. Connect no prediction endpoint until sufficient compatible, genuine surveillance and weather data exist and a reproducible model passes chronological evaluation against a baseline.

## Proposed source and model architecture

- NASA POWER daily point adapter: documented parameters `PRECTOTCORR`, `T2M`, `T2M_MAX`, `T2M_MIN`, `RH2M`; retain source, coordinates, units, daily observation date, retrieval time, and cache results. A supplied local POWER export is useful for offline checks.
- OpenDengue: optional CSV ingestion with source interval preserved. No current case data means no model-ready disease series.
- Satellite, WorldPop, boundary, mobility, water and vector indicators: register as unavailable until an appropriate licensed product, region crosswalk, and processing path is configured. Never fabricate substitutes.
- Sensor ingest: per-kit hashed bearer token, registered location, Pydantic validation, idempotency key, range/unit checks, persisted received time, and last-seen status. Keep admin operations disabled absent a configured admin token.
- Feature/model work: align exact reporting intervals and source availability dates; chronological evaluation; baseline first; store artifacts only when supportable.

## API contracts needed

Base path `/api/v1`; JSON timestamps ISO-8601 UTC.

- `GET /health` and `/api/v1/health`: service and database readiness.
- `GET /api/v1/locations`: registered location IDs and display names.
- `GET /api/v1/dashboard/summary?location_id=...`: observed case/weather/sensor coverage and explicit prediction availability.
- `GET /api/v1/dashboard/timeseries?location_id=...&start=...&end=...`: dated observations with source, value, unit and quality metadata.
- `GET /api/v1/data-sources/status`: source status, last fetch, latest observation, coverage and configuration requirements.
- `GET /api/v1/kits` and `/kits/{kit_id}/status`: public kit identity/status only, never credentials.
- `POST /api/v1/ingest/sensor-readings`: authenticated readings with `kit_id`, timestamp, idempotency key, and typed measurements.
- Prediction endpoints are intentionally unavailable until valid training data and evaluation exist.

## Dependencies and configuration

Retain Streamlit/pandas/scikit-learn for the existing application. Add FastAPI, Uvicorn, SQLAlchemy, Pydantic settings and HTTP client for the API. Optional geospatial processing libraries should not be mandatory for basic service startup. `.env.example` should cover database URL, CORS origins, frontend API URL, admin token, and NASA POWER timeout/cache paths; no credentials are committed.

## Risks, gaps, and priorities

1. Verify local raster metadata (bounds/CRS/nodata/year) and POWER CSV headers/units before using them.
2. No genuine disease surveillance data currently exists in the repo; do not claim a trained genuine outbreak predictor.
3. Static frontend mocks can be mistaken for real public-health warnings; label demo fallback visibly and suppress numeric sample risk as an operational claim.
4. District-level joins require stable IDs and a documented boundary vintage; city names alone are insufficient.
5. Do not call unconfigured third-party data sources on every page load. Cache and isolate adapters.
6. Device credential provisioning and rate limiting need production deployment controls; local prototype tokens are not a production identity system.

Implementation priority: API/database/sensor ingestion and source status; documented POWER adapter and CSV import contracts; frontend live/unavailable states; then verified surveillance alignment and model training only after source coverage supports it.
