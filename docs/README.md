# Argus docs

Start here. Pick the guide that matches what you need to do.

## Guides

- [CONNECT-THE-HAT.md](CONNECT-THE-HAT.md): step by step, from a blank SD card to your first real incident report.
- [TEAM-HANDOFF.md](TEAM-HANDOFF.md): how to swap in the hardware team's sensor code, and how to hot-swap, shadow and test a module.
- [TUNING.md](TUNING.md): how to record labelled runs and tune the detector thresholds from real data.

## Reference

- [../.plans/argus-mvp-spec.md](../.plans/argus-mvp-spec.md): the MVP spec. Hardware (section 2, including the GPIO table in 2.3), detectors (4), Pi software (5), backend (6) and the demo runbook (7).
- [../.plans/argus-build-plan-spec.md](../.plans/argus-build-plan-spec.md): the build plan and milestones.
- [../contracts/api.md](../contracts/api.md): the device-to-backend API (pairing, `GET /api/devices/me`, event upload).
- [../contracts/event.schema.json](../contracts/event.schema.json), [../contracts/report.schema.json](../contracts/report.schema.json), [../contracts/sensor_trace.schema.json](../contracts/sensor_trace.schema.json): JSON Schemas for events, reports and sensor traces.
- [../contracts/device_queue.md](../contracts/device_queue.md): how the device queues events before upload.
- [../device/hardware/argus/README.md](../device/hardware/argus/README.md): our hardware drivers, their options, and the bench checklist.
- [../device/hardware/team/](../device/hardware/team/): one folder per module for the team's code. Each folder has a `README.md` and an `adapter.py`.
- [../device/hardware/replay/FORMAT.md](../device/hardware/replay/FORMAT.md): the `.jsonl` recording format and the `.labels.json` sidecar.
- [../device/config.yaml](../device/config.yaml): the config template. The installer copies it to `/etc/argus/config.yaml` on the Pi.

## Quick start on a laptop (no hardware)

You can run the whole system on a laptop with simulated sensors. You do not need a Pi.

```bash
./scripts/demo-e2e.sh
```

Until that script exists, these commands work today. They run from the repo root.

Device side (simulated sensors, no Pi needed):

```bash
cd device
uv sync                                  # install the laptop dependencies (no Pi libraries)
uv run argus-cli scenario --list         # list the simulated scenarios
uv run argus-cli run --duration 10       # run the device for 10 seconds, then stop
uv run pytest -q                         # run the unit tests
```

Backend and dashboard side:

```bash
pnpm install                             # from the repo root
pnpm db:start                            # start the local Supabase stack (needs Docker)
cd web && pnpm dev                       # start the dashboard on localhost
```

The `pnpm db:*` scripts are in the root `package.json`. `pnpm dev` is in `web/package.json`.
