# ClimateGuard

ClimateGuard currently includes the original Streamlit research prototype, a static multi-page AarogyaSight dashboard, and a FastAPI service. The UI design is retained. The API withholds predictions because there is no genuine compatible dengue surveillance dataset configured in this repository.

## What it does
- Loads a location-specific historical CSV.
- Checks whether observations appear weekly (does not fabricate weekly case records).
- Visualizes reported cases and weather.
- Trains a Random Forest model for a prototype target: elevated cases in the next week.
- Reports chronological holdout metrics and feature importance.
- Includes synthetic demo data solely to test the interface.

## Important limitations
This is not a validated public-health warning system. The elevated-activity threshold is a research placeholder, not an official outbreak definition. A single location time series and a small holdout are not enough to establish operational reliability. Do not use the output to make clinical decisions or declare outbreaks.

## Install
Python 3.10+ recommended.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Run the API and dashboard

From this directory, configure a long random `ADMIN_TOKEN` (see `.env.example`; load it in your shell or deployment environment), then run:

```bash
uvicorn api:app --reload --port 8000
```

Open API docs at `http://127.0.0.1:8000/docs`. To serve the existing frontend, in a second terminal run `python -m http.server 8080 --directory "../Frontend/aarogyasight-html-css-js/aarogyasight"` from this directory. Set `CORS_ORIGINS=http://localhost:8080,http://127.0.0.1:8080` for that local server. The frontend checks health/source status and keeps its established visual layout; its sample fallback remains marked as demo data.

See [docs/API.md](docs/API.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), and [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md). Register a location and individual kit using the authenticated admin endpoints, then submit sensor readings using a kit-specific bearer credential. Use bounded requests for NASA POWER imports. The service stores local data in `data/climateguard.sqlite3` by default.

For kit buffering, retries, and ESP32 payload guidance, see [docs/DEVICE_INTEGRATION.md](docs/DEVICE_INTEGRATION.md).

Run API tests with `pytest -q` after installing requirements. Tests use an isolated temporary SQLite database and do not call external APIs.

## Input CSV format
One row per location per weekly period. Required columns:
- `date`: date identifying the weekly period
- `cases`: reported dengue case count for that period
- `rainfall_mm`: weekly rainfall total (mm)
- `temperature_c`: weekly mean temperature (°C)

Optional:
- `humidity_pct`
- `population`
- `location`

Example header:

```csv
date,cases,rainfall_mm,temperature_c,humidity_pct,location
2022-01-02,12,4.5,26.2,61,District A
```

The example is a schema illustration, not real data.

## Data acquisition plan
1. Visit https://opendengue.org/data.html and download an appropriate temporal or spatial extract.
2. Inspect `adm_0_name`, `adm_1_name`, `adm_2_name`, `calendar_start_date`, `calendar_end_date`, `dengue_total`, and source/case-definition fields.
3. Select a geography only after verifying enough consistent historical observations. Do not assume district-level weekly data exists.
4. Retrieve daily climate variables from NASA POWER's documented API: https://power.larc.nasa.gov/docs/services/api/temporal/daily/
5. Aggregate weather to the same reporting periods as cases and document spatial resolution. Never split monthly cases into fake weekly records.
6. Create a cleaned CSV with the schema above. The app's MVP expects one selected location and a consistent weekly cadence.

## Suggested team split
- Data lead: source audit, clean cases, match climate, document licenses/case definitions.
- ML lead: baseline, time-based validation, model metrics, leakage checks.
- App lead: Streamlit interface, plots, user interaction.
- Integration lead: test end-to-end, GitHub/README, presentation, limitations.

## 48-hour execution
- Hours 0–4: data coverage audit and choose one valid location/time scale.
- Hours 4–12: prepare a consistent dataset; plot cases and climate.
- Hours 12–22: baseline and ML model with chronological holdout.
- Hours 22–34: dashboard integration and risk explanation.
- Hours 34–42: data-quality checks, test, compare against baseline.
- Hours 42–48: demo script, slides, limitations and source citations.

## Next improvements
- Compare Random Forest against a seasonal-naive baseline.
- Add calibrated probabilities and confidence intervals.
- Use a defined official/local outbreak threshold where available.
- Add multiple locations only when geographic and temporal matching is valid.
- Add WorldPop population estimates and satellite features after the core model is validated.
