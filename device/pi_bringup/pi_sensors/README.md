# Argus pi_sensors — SCD41 air + VL6180X wear

Package lives under the Argus monorepo: `~/argus/pi_sensors`.

Emits NDJSON to `logs/air_events.jsonl` and `logs/wear_events.jsonl`.

**Do not** run the VL6180X here at the same time as `hat.main` / `test_all_at_once` (both use I2C bus 3 ToF). Prefer hat’s in-process ToF for wear-gated fall; use this package for SCD41 air and standalone ToF bring-up.

## Setup

```bash
cd ~/argus/pi_sensors
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

## Run

```bash
# Hardware check
python tools/check_hardware.py

# SCD41 only (safe alongside hat ToF)
python -m argus_sensors --sensors scd41 --scd-bus 1 --log-dir logs

# Full ToF + SCD (standalone; stop hat first)
python -m argus_sensors --tof-bus 3 --scd-bus 1 --log-dir logs

# Tests
pytest
```

## Sample JSON

Canonical shapes for friends/frontend: `~/argus/samples/` (`air_sample.sample.json`, `wear_event.sample.json`).
