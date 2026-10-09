# Local kit integration

1. Set `ADMIN_TOKEN`, start the API and register a location and kit with the admin routes in [API.md](API.md).
2. Provision the unique kit credential into secure device storage over a trusted setup path. Do not put `ADMIN_TOKEN` in firmware or source control.
3. For each sample, send JSON to `POST /api/v1/ingest/sensor-readings` with `Authorization: Bearer <kit-token>`, UTC timestamp with an explicit offset, a unique persistent `message_id`, and only the measurements that sensors actually provide.
4. Persist queued readings locally until a successful HTTP response. Retry transient errors with exponential backoff and reuse the same `message_id`; duplicates are safe. A 401 means the credential is invalid/revoked; 422 means correct the timestamp, unit, or value.
5. Check `GET /api/v1/kits/{kit_id}/status` for `last_seen`; credentials are never returned by status endpoints.

Example ESP32 Arduino sketch outline (adapt sensor drivers and TLS trust to deployment):

```cpp
// Store KIT_TOKEN in secure provisioning/NVS, not in a committed sketch.
HTTPClient http;
http.begin(apiUrl + "/api/v1/ingest/sensor-readings");
http.addHeader("Authorization", "Bearer " + kitToken);
http.addHeader("Content-Type", "application/json");
String payload = "{\"kit_id\":\"kit-nagpur-01\",\"message_id\":\"" + messageId +
  "\",\"timestamp\":\"" + utcTimestamp +
  "\",\"temperature_c\":{\"value\":" + String(tempC, 2) + ",\"unit\":\"C\"}}";
int status = http.POST(payload);
// Remove queued item only after 2xx. Retry network/5xx with backoff and the same messageId.
http.end();
```

Production deployments must configure TLS certificate validation, rotate/revoke kit credentials, and add an operational rate limit at the gateway. Do not send personal identifiers or precise household information.
