# ClimateGuard prototype

For the new, separate FastAPI + dynamic frontend implementation, follow the [ClimateGuard Platform quick start](climateguard_platform/README.md). The new work is developed on branch `climateguard-platform`.

The repository includes a FastAPI ingestion/data API and the existing Streamlit research prototype under [climateguard_mvp](climateguard_mvp/README.md), plus the pre-existing static AarogyaSight dashboard under [Frontend](Frontend/aarogyasight-html-css-js/aarogyasight/README.md).

Start with [the project audit](climateguard_mvp/docs/PROJECT_AUDIT.md), then follow [API setup and examples](climateguard_mvp/docs/API.md). The frontend's established styles and page structure remain. It reports backend/source state when the API is available and clearly labels its static preview values as demo data.

There is no genuine dengue surveillance extract configured yet. Prediction endpoints therefore report unavailable. NASA POWER can be imported for registered coordinates and bounded date periods; IoT readings are validated and persisted with per-device credentials. Satellite, population, mobility, water, vector, automated schedules, and production-grade rate limiting remain future work pending verified datasets/deployment needs. Details and provenance requirements are in [DATA_SOURCES.md](climateguard_mvp/docs/DATA_SOURCES.md) and [MODEL_CARD.md](climateguard_mvp/docs/MODEL_CARD.md).
