# AarogyaSight — HTML, CSS & JavaScript

Open index.html in a browser. No React, package installation, or build step is required. All pages are included, along with local photos and the India state map. Fonts load from Google Fonts when online.

## Files
- index.html: dashboard
- disease-map.html, predictions.html, climate-data.html, iot-sensors.html, insights.html, reports.html, about.html: linked pages
- styles.css: complete shared styles and semantic color tokens
- app.js: disease tabs, district selection, map zoom, search, notifications, forecast period, CSV export, and navigation
- assets/: local images and favicon

## Backend connection
The shared `app.js` checks the optional ClimateGuard API at `http://127.0.0.1:8000` (override with `window.CLIMATEGUARD_API_BASE` before loading `app.js`). It displays backend/source status, fetched NASA POWER observations, and last-seen kit readings when they exist. If no API is reachable, the original static preview remains clearly labeled demo data. If the API is reachable but validated surveillance/model data are unavailable, risk values and forecast curves are suppressed and show unavailable states. Map coloring and remaining unconnected content are illustrative, not district-level prediction. Predictions are not medical advice.

## Data attribution
India boundaries: Amazing-coder1203/BharatMaps (public GeoJSON), simplified for display. Neighboring countries: johan/world.geo.json. City image is AI-generated illustrative imagery, not a verified location photo.
