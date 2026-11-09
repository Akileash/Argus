# Argus Pi handoff

Proven hard-hat sensors. In the GitHub monorepo this folder is **`device/pi_bringup/`** on branch **`hardware`**.

Friends: read repo `docs/PI-BRINGUP.md`, then parse `samples/` here.  
Hardware truth (DRV2605L, VL6180X, pins): `docs/HARDWARE-DIFF.md` + `argus_llm_context.md` §4.

## Folder map

```
device/pi_bringup/   (or ~/argus on the Pi)
  hat/                 # canonical runtime: IMU, US, haptics, camera, ToF wear gate, fall
  pi_sensors/          # SCD41 air (+ optional standalone VL6180X)
  samples/             # clean JSON — parse these first
  events.jsonl         # LIVE hat log (gitignored; do not ship)
  export_backend.py    # hat events → POST /events outbox JSON
  test_all_at_once.py  # all-sensor load test
  validate_wear_gated_fall.py
  HANDOFF.md           # this file
```

**Legacy (non-canonical):** root scripts `imu_fall.py`, `hcsr04_lof.py`, `hcsr04_test.py`, `mpu6050_test.py` — older bring-up; use `hat/` instead.

Team plug-in copies: `device/hardware/team/<module>/`.

## Hardware / buses

| Bus | Expect (`i2cdetect -y N`) | Devices |
|-----|---------------------------|---------|
| 1 | `5a`, `62` | LEFT DRV2605L, SCD41 |
| 3 | `5a`, `6b`, `1d`, `29` | RIGHT DRV, MinIMU-9 gyro, MinIMU-9 accel/mag, VL6180X |

Overlay: `dtoverlay=i2c3,pins_4_5` in `/boot/firmware/config.txt`.

## Sensor → code → JSON

| Sensor | Code | Output |
|--------|------|--------|
| MinIMU-9 fall | `hat/minimu9.py` + `hat/imu.py` → `hat/events.py` | `type: fall` in `events.jsonl` / `samples/fall.sample.json` |
| HC-SR04 LoF | `hat/ultrasonic.py` | `type: line_of_fire` / `samples/line_of_fire.sample.json` |
| VL6180X wear | `hat/tof.py` (+ optional `pi_sensors`) | status lines in hat; NDJSON `wear_event` in `pi_sensors/logs/` / `samples/wear_event.sample.json` |
| SCD41 air | `pi_sensors` `argus_sensors` | `air_sample` → `pi_sensors/logs/air_events.jsonl` / `samples/air_sample.sample.json` |
| DRV2605L | `hat/haptics.py` | no event JSON (local buzz) |
| Pi Cam | `hat/camera.py` | `clip_path` on fall events |

**Fall rules (current):** fall-only (hard hit or freefall path). Wear gate: 3 s lookback, ≥ 50% ToF ON at onset. No `impact` / `hat_removed` from live hat.

**Do not** dual-run ToF: hat and `python -m argus_sensors --tof-bus 3` must not both own bus 3.

## JSON for frontend / DB

1. Read `samples/README.md` and the `*.sample.json` files.
2. Hat alert events: `event_id`, `hat_id`, `type`, `timestamp`, `sensor{...}`.
3. Wear-gated fall extras under `sensor`: `helmet_worn_at_onset`, `helmet_worn_lookback_s`, `wear_on_fraction`.
4. Backend POST shape: `samples/backend_event.sample.json` (from `export_backend.py`). Wear fields also appear under `sensor.detail`.
5. Air / wear streams are separate NDJSON (flat fields, `t_ms`, `received_at`) — not the same schema as hat `events.jsonl`.
6. Ignore hat status spam / suppressed-gate log lines; only ingest JSON objects with known `type` values.

Mixed pipeline smoke file: `samples/events.sample.jsonl`.

## Runbook

```bash
cd ~/argus && source .venv/bin/activate

# Full hat
python -m hat.main

# All sensors (ToF in-process + SCD subprocess + IMU + US + haptics)
python test_all_at_once.py --seconds 30

# Air only (safe with hat ToF)
cd pi_sensors && source .venv/bin/activate
python -m argus_sensors --sensors scd41 --scd-bus 1 --log-dir logs

# Export hat events for backend
cd ~/argus && python export_backend.py --last 5

# Tests
cd ~/argus && pytest hat/tests
cd ~/argus/pi_sensors && .venv/bin/pytest
cd ~/argus && python validate_wear_gated_fall.py
```

Systemd unit (optional): `pi_sensors/deploy/argus-sensors.service` (paths already point at `~/argus/pi_sensors`).

## Frontend notes

- Prefer `event_id` for idempotent upserts.
- Clips upload separately; `clip_pending` may be true on fall.
- LoF has `side` (`left`/`right`) and US fields under `sensor`.
- Sample numbers are representative of real Pi readings; trim `imu_trace` further if Wi‑Fi payloads are tight (`export_backend` already caps length).

## Do / don’t

- Do parse `samples/` first.
- Do keep SCD on bus 1 via `pi_sensors` while hat owns ToF on bus 3.
- Don’t ship live `events.jsonl` or `pi_sensors/logs/`.
- Don’t dual-init VL6180X.
- Don’t treat legacy root scripts or old `impact` outbox files as the current contract.
