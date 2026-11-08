# Contract bridge — Pi JSON ↔ `contracts/event.schema.json`

Friends: our **working** payloads live in `device/pi_bringup/samples/`.  
The cloud/device contract is `contracts/event.schema.json`. They are **not identical yet**. This table is the map.

## Hat alert events (pi_bringup)

| Our field / value | Contract direction |
|-------------------|--------------------|
| `event_id` (uuid) | `event_id` |
| `hat_id` | → `device_serial` (pairing later) |
| `type: fall` | `type: fall` + `device_class: incident` |
| `type: line_of_fire` | closest: `struck_by_near_miss` / `struck_by_contact` (needs product rule) |
| `timestamp` | → `t0` / `detected_at` (UTC normalize in uploader) |
| `sensor.peak_accel_g` | → `metrics.peak_g` |
| `sensor.freefall_ms` | → `metrics.free_fall_ms` |
| `sensor.distance_m` / `closing_speed_mps` | → `metrics.range_m` / `closing_rate_mps` |
| `sensor.helmet_worn_at_onset` / `wear_on_fraction` | → `metrics.hat_on` (+ detail) |
| `side` left/right | → `metrics.sensor_id` R2/R3 |
| `clip_path` / `clip_pending` | → `media[]` after hash/size known |
| `export_backend` outbox JSON | intermediate; still uses older hat/backend keys |

## Streams (not full ArgusEvent)

| Our NDJSON | Maps to module samples |
|------------|------------------------|
| `wear_event` / ToF | `hat_on` team samples (`distance_mm`, status) |
| `air_sample` | `env` team samples (`co2_ppm`, `temp_c`, `rh_pct`) |

## Types we do **not** emit live from hat today

`impact`, `hat_removed`, `hat_knocked_off`, `struck_by_*`, `module_fallback` — software detectors may still produce these; pi_bringup fall path is wear-gated **fall-only**, LoF is `line_of_fire`.

## Next integration work (after this push)

1. Uploader adapter: map pi_bringup / export_backend → contract `event.json`.
2. Decide LoF ↔ struck_by naming with PM.
3. Keep `samples/` as fixtures until the adapter is green.
