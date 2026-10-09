# Data sources and freshness

## NASA POWER

Adapter: daily point endpoint at `https://power.larc.nasa.gov/api/temporal/daily/point`. Requests `PRECTOTCORR` (mm/day), `T2M`/`T2M_MAX`/`T2M_MIN` (°C), and `RH2M` (%), for a registered coordinate and explicit start/end dates. These are gridded/reanalysis estimates, not local live observations or forecasts. `retrieved_at` is distinct from `observed_on`. POWER sentinel values are stored as missing. Source documentation: https://power.larc.nasa.gov/docs/services/api/temporal/daily/ .

The repository includes POWER CSV exports, but they have not yet been cross-checked against registered boundaries or disease reports. They are not automatically treated as observations in the database.

## Dengue surveillance

No OpenDengue extract is currently configured in this repository. The API provides an explicit CSV import requiring a stable `location_id` crosswalk and preserves each interval exactly as reported. Exact duplicates are idempotent; interval overlaps are flagged for review and not combined. Import future source files only after confirming actual geographic and temporal coverage, case-definition/source metadata, duplicate/overlap risk, and reporting cadence. Never split monthly reports into weekly counts. OpenDengue: https://opendengue.org/data.html .

## Other sources

Satellite products, WorldPop, administrative boundaries, human mobility, water-resource observations, and direct vector surveillance are not currently ingested. They remain explicitly unavailable. A population raster file alone does not establish its boundary alignment or year-specific suitability. Mobility must be privacy-preserving and authorized; no proxy is displayed as measured mobility. Surface-water products are not mosquito-breeding observations.

## Local sensors

Sensor timestamps are device observation time; `received_at` is backend receipt time. Kit values are site-level measurements and should not be generalized to a district. Kits may include temperature, humidity, rainfall, water level, and battery status. Actual installation/sensor calibration details must be recorded operationally.
