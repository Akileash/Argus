# Device ↔ backend API contract (v1)

Owned by the PM. Implemented by `web/app/api/**` (M4.2), consumed by `device/argus/uploader.py` (M2.4). Bodies validate against the JSON Schemas in this folder.

All device calls send `Authorization: Bearer <device_token>`. The token is shown once when a device is registered in the dashboard. Only its SHA-256 is stored, in `devices.token_hash`. Base URL comes from `device/config.yaml → backend.base_url` (blank by default; local dev = `http://localhost:<port>`).

| # | Method + path | Body → Response | Notes |
|---|---|---|---|
| 1 | `POST /api/devices/register` | *(dashboard session, manager role)* `{serial, org_id}` → `201 {device_id, device_token}` | Called from the pairing page. The token is never retrievable again. |
| 2 | `GET /api/devices/me` | → `200 {device_id, serial, org_id, assigned_worker: {id, full_name} \| null, server_time}` | Pairing check (`argus-cli pair --check`). `server_time` lets the device sanity-check clock mapping. |
| 3 | `POST /api/events` | `ArgusEvent` (event.schema.json) → `201 {event_id, status, uploads: [{kind, filename, signed_url, expires_at}]}` | Idempotent on `event_id`: a repeat returns `200` with fresh signed URLs for media not yet confirmed. `hat_removed` → `uploads: []` and `status: "ready"` immediately (no report). Validation failure → `422 {errors}`. |
| 4 | `PUT <signed_url>` | raw file bytes | Directly to Supabase Storage bucket `event-media`, path `<org_id>/<event_id>/<filename>`. |
| 5 | `POST /api/events/{event_id}/complete` | `{uploaded: [filename…]}` → `200 {status}` | Backend verifies the objects exist (size + sha256 from the event body), sets `events.status = 'uploaded'`, and the worker picks it up. The device deletes its local copy **only after this 200** (spec §4.4: ack-then-delete). |

Event status lifecycle (`events.status`):
`received → uploading → uploaded → analyzing → ready | failed`
(`hat_removed` and `module_fallback` go straight `received → ready`).

Report status (`reports.status`): `draft → signed` (signed = locked).

Errors: JSON `{error: {code, message}}`. Device retries `5xx`/network with exponential backoff (1s → 60s cap, jitter). It does not retry `4xx` except `401` (re-read config) and `409`.
