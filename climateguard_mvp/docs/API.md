# API reference

Base URL is the FastAPI host. Interactive OpenAPI documentation is available at `/docs`.

## Health and dashboard

- `GET /api/v1/health` returns service and database status; the prediction field explicitly indicates no validated model.
- `GET /api/v1/locations` returns registered location IDs, names and coordinates.
- `GET /api/v1/dashboard/summary?location_id={id}` returns sensor freshness and prediction availability. Case counts are unavailable until surveillance is imported.
- `GET /api/v1/dashboard/timeseries?location_id={id}&start=YYYY-MM-DD&end=YYYY-MM-DD` returns NASA POWER daily observations with source/retrieval metadata.
- `GET /api/v1/data-sources/status` reports configured and unavailable sources.
- `GET /api/v1/predictions` returns an empty unavailable result until a validated model and its training data exist.

## Kits

`POST /api/v1/ingest/sensor-readings` requires `Authorization: Bearer <kit-credential>`. Example:

```json
{
  "kit_id": "kit-nagpur-01",
  "message_id": "boot42-000019",
  "timestamp": "2026-10-09T10:00:00Z",
  "temperature_c": {"value": 29.4, "unit": "C"},
  "humidity_pct": {"value": 76, "unit": "%"},
  "rainfall_mm": {"value": 0.4, "unit": "mm"},
  "quality": "sensors_ok"
}
```

Duplicate `(kit_id,message_id)` returns `status: duplicate`. Read-only dashboard endpoints: `GET /api/v1/kits`, `/kits/{id}/status`, `/kits/{id}/readings`. They never return credentials.

## Admin setup and ingestion

Send `X-Admin-Token` on admin calls. Register a location with `POST /api/v1/admin/locations` JSON `{ "id":"nagpur", "name":"Nagpur", "admin_name":"Maharashtra", "latitude":21.1458, "longitude":79.0882 }`. Register a kit with `POST /api/v1/admin/kits` JSON `{ "id":"kit-nagpur-01", "location_id":"nagpur", "credential":"<unique random token of at least 24 chars>" }`. Store the credential securely; it cannot be retrieved later.

Revoke a device credential with `POST /api/v1/admin/kits/{kit_id}/revoke`. Revoked devices can no longer submit readings.

Import reported dengue records with `POST /api/v1/admin/opendengue/import?source_key=<extract-id>` as multipart field `file` (UTF-8 CSV, max 10 MB / 100,000 rows). Required columns are `location_id,calendar_start_date,calendar_end_date,dengue_total`. `location_id` must match a registered stable ID; there is no fuzzy-name join. Optional columns: `source_name,case_definition,adm_2_name,adm_1_name,location_name`. Exact duplicate interval rows for the same location/source key are idempotent. Overlapping intervals, even across extracts, are stored with a review flag and never combined. `GET /api/v1/locations/{location_id}/observations` returns source intervals as reported. The source status reports loaded coverage; this does not imply validated forecasting suitability.

`POST /api/v1/admin/power/{location_id}?start=2024-01-01&end=2024-01-31` fetches the explicit bounded interval from NASA POWER. Failure is isolated and recorded. Do not automate broad backfills on every web request.

Unknown locations/kits return 404, invalid requests return 422, unauthenticated devices return 401, and duplicates are idempotent.
