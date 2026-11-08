# Argus sample JSON

Parse these first. Do **not** ingest the live `~/argus/events.jsonl` or `pi_sensors/logs/*.jsonl` for schema work — those are large runtime logs.

| File | Sensor / source | Notes |
|------|-----------------|-------|
| `fall.sample.json` | MPU-6050 (+ ToF wear gate) | Canonical `type: fall` from `hat/events.py` |
| `line_of_fire.sample.json` | Dual HC-SR04 | Threat from left/right ultrasonic |
| `wear_event.sample.json` | VL6180X ToF | Helmet ON/OFF from `pi_sensors` or hat ToF status |
| `air_sample.sample.json` | SCD41 | CO₂ / temp / RH |
| `events.sample.jsonl` | Mixed | One line per event for pipeline smoke tests |
| `backend_event.sample.json` | `export_backend.py` | POST `/events` body shape |

Hat events use `event_id` + nested `sensor`. Air/wear NDJSON from `pi_sensors` use flat fields + `t_ms` / `received_at`.
