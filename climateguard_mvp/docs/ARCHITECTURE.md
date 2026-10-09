# ClimateGuard architecture

The existing Streamlit research tool and static AarogyaSight frontend remain in place. `api.py` is an independent FastAPI service; SQLite is the local prototype store. Its schema retains location identity, device credential hashes, observation and receipt timestamps, readings, source jobs, and NASA POWER observations. Device tokens are individual to a kit and only SHA-256 hashes are stored. Administrative registration/import routes require `X-Admin-Token`; the credential is never returned by public kit endpoints.

NASA POWER daily point data can be imported for a registered coordinate through a bounded explicit date range. Requests use a timeout, documented daily parameters, UTC dates and store invalid POWER sentinel values as null. Source jobs record success or failure. Disease surveillance, satellite features, population, boundaries, mobility, and vector observations are reported unavailable until validated inputs and adapters are configured. In particular, no model is trained without genuine compatible surveillance data.

Local run: install `requirements.txt`, set `ADMIN_TOKEN` and optional `CORS_ORIGINS`, then run `uvicorn api:app --reload` from this directory. The service initializes the database on startup/import. Serve the static frontend with a local HTTP server and configure its API base. Do not expose this development configuration publicly.

Sensor firmware should persist a monotonically unique message ID and unsent payloads locally, then retry the same message ID with exponential backoff after network failures. The API accepts explicit UTC offsets, validates measurement ranges/units, and handles duplicate IDs idempotently. Provision each device credential out of band; never put admin credentials in firmware.
