# Pi bring-up (hardware branch) — start here

This branch delivers the **proven Raspberry Pi hard-hat stack** so teammates can integrate without guessing sensors.

## Where to look

| Path | Role |
|------|------|
| [`device/pi_bringup/`](../device/pi_bringup/) | Full working tree from the Pi (`hat/`, `pi_sensors/`, `samples/`) |
| [`device/pi_bringup/HANDOFF.md`](../device/pi_bringup/HANDOFF.md) | Runbook, buses, JSON field map |
| [`device/pi_bringup/samples/`](../device/pi_bringup/samples/) | Canonical JSON for fall, LoF, wear, air, backend POST |
| [`device/hardware/team/*/`](../device/hardware/team/) | Plug-in surface (vendor + mapping + sample) per TEAM-HANDOFF |
| [`docs/HARDWARE-DIFF.md`](HARDWARE-DIFF.md) | Our wired sensors vs older software docs (**ours is truth**) |
| [`docs/CONTRACT-BRIDGE.md`](CONTRACT-BRIDGE.md) | Our event JSON ↔ `contracts/event.schema.json` |
| [`argus_llm_context.md`](../argus_llm_context.md) | Project context — hardware § updated to this build |

## Pull this branch

```bash
git fetch origin
git switch hardware
```

## Run on the Pi (bring-up)

```bash
cd device/pi_bringup
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -r requirements.txt
# SCD / ToF package:
cd pi_sensors && python3 -m venv --system-site-packages .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt && cd ..

python test_all_at_once.py --seconds 30
python -m hat.main
```

**Do not** dual-run VL6180X: hat ToF and `python -m argus_sensors --tof-bus 3` must not both own I2C3.

## Teammate integration next

1. Read each `device/hardware/team/<module>/HANDOFF-NOTES.md`.
2. `cd device && uv run argus-cli team check <module> --sample <module>/sample.txt`
3. Wire camera `save_command` to real `hat.camera.ClipRecorder` when ready.
4. After confirm, PR **`hardware` → `software`** (not empty `main` first).
