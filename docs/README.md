# Argus docs

Start here. Pick the guide that matches what you need to do.

## Guides

- [CONNECT-THE-HAT.md](CONNECT-THE-HAT.md): step by step, from a blank SD card to your first real incident report.
- [../CONTRIBUTING.md](../CONTRIBUTING.md): who owns which folder, branch names, the one check command (`scripts/check.sh`) and how to resolve a lockfile conflict.
- [TEAM-HANDOFF.md](TEAM-HANDOFF.md): how to plug the hardware team's sensor script in with a mapping file (no code), check it without hardware, and hot-swap, shadow and test a module.
- [TUNING.md](TUNING.md): how to record labelled runs and tune the detector thresholds from real data.

## Reference

- [../.plans/argus-mvp-spec.md](../.plans/argus-mvp-spec.md): the MVP spec. Hardware (section 2, including the GPIO table in 2.3), detectors (4), Pi software (5), backend (6) and the demo runbook (7).
- [../.plans/argus-build-plan-spec.md](../.plans/argus-build-plan-spec.md): the build plan and milestones.
- [../contracts/api.md](../contracts/api.md): the device-to-backend API (pairing, `GET /api/devices/me`, event upload).
- [../contracts/event.schema.json](../contracts/event.schema.json), [../contracts/report.schema.json](../contracts/report.schema.json), [../contracts/sensor_trace.schema.json](../contracts/sensor_trace.schema.json): JSON Schemas for events, reports and sensor traces.
- [../contracts/device_queue.md](../contracts/device_queue.md): how the device queues events before upload.
- [../device/hardware/argus/README.md](../device/hardware/argus/README.md): our hardware drivers, their options, and the bench checklist.
- [../device/hardware/team/](../device/hardware/team/): one folder per module for the team's code. Each folder has a `README.md`, a `vendor/` folder for the original script, a `mapping.yaml` (made with `argus-cli team init <module>`) and a small `adapter.py`. Teammates edit only their own folder.
- [../device/hardware/replay/FORMAT.md](../device/hardware/replay/FORMAT.md): the `.jsonl` recording format and the `.labels.json` sidecar.
- [../device/config.yaml](../device/config.yaml): the shared config template. The installer copies it to `/etc/argus/config.yaml` on the Pi. Machine-specific choices (such as `impl: team`) go in the ignored `device/config.local.yaml`, which is merged over it; see TEAM-HANDOFF.md section 2.

## Report worker settings

The worker (`worker/`) reads these from the environment (`.env.example` lists them). All are optional.

| Variable | Default | What it does |
| --- | --- | --- |
| `REPORT_MODE` | `fixture` | `fixture` (no keys, no video model), `gemini`, or `hybrid` (Gemini watches, Claude writes). |
| `LLM_SPEND_CAP_USD` | `25` | Hard stop for total LLM spend recorded in `analyses`. Past it, events get a free sensor-only draft that says the cap was reached. |
| `WORKER_MIN_CLIP_BYTES` | `50000` | A clip counts only if its `media` row is `confirmed` and at least this big (and the downloaded file too). Otherwise the report is written from sensor data only and says why in "Confidence and gaps". A real 30 s 720p clip is megabytes; the mock camera's 2 s clips are about 130 KB. |
| `GEMINI_PROCESSING_TIMEOUT_S` | `120` | How long to wait for Gemini to finish processing an uploaded video. On a timeout or a `FAILED` file the video is not retried: sensor-only report, reason recorded in the report and in an `analyses` row (`video_skipped`). The uploaded file is always deleted. |
| `LLM_CALL_TIMEOUT_S` | `180` | Time limit for each single provider step (Gemini upload, Gemini generate, Claude request). A call that hangs is not retried. For Gemini (and for Claude in hybrid) a timeout does not fail the event: the report is written from sensor data and says why. |
| `GEMINI_TRANSIENT_RETRIES` | `3` | Tries per Gemini call on transient errors (HTTP 429/500/502/503/504, `UNAVAILABLE`, `RESOURCE_EXHAUSTED`, "high demand", network errors), the first included. `1` turns retrying off. If Gemini is still unavailable, `hybrid` has Claude write the report from sensor data and `gemini` writes the sensor-only report; either way `confidence_and_gaps` says the video could not be analysed and why, and an `analyses` row `video_skipped` is stored. |
| `GEMINI_TRANSIENT_BACKOFF_MS` | `10000` | Delay before the second try; doubles after that, plus up to 25% jitter (about 30-38 s in all with the default 3 tries). |
| `WORKER_MAX_ANALYSIS_ATTEMPTS` | `3` | Total claims per event (`events.analysis_attempts`), reclaims included. After that the event is `failed` with an error and is never picked up again. To retry one by hand, set its status to `uploaded` and `analysis_attempts` to `0`. |
| `WORKER_HEARTBEAT_MS` | `30000` | How often a working worker refreshes `events.updated_at`. |
| `WORKER_STALE_ANALYZING_MS` | computed, `1560000` (26 min) | An `analyzing` event with no heartbeat for this long is re-queued (if it has claims left). Default = 2 x (180 + 120 + 30 + 180) + 180 + 60 + 300 s: two Gemini calls and one Claude call (the longest hybrid path), a download allowance and a 5 min margin. |
| `WORKER_POLL_MS`, `WORKER_RETRY_DELAY_MS` | `2000`, `3000` | Poll interval, and the pause before the one in-claim retry (retryable errors only). |

## Quick start on a laptop (no hardware)

You can run the whole system on a laptop with simulated sensors. You do not need a Pi.

```bash
./scripts/demo-e2e.sh
```

To run the pieces by hand instead, use these commands. They run from the repo root.

Device side (simulated sensors, no Pi needed):

```bash
cd device
uv sync                                  # install the laptop dependencies (no Pi libraries)
uv run argus-cli scenario --list         # list the simulated scenarios
uv run argus-cli run --duration 10       # run the device for 10 seconds, then stop
uv run pytest -q                         # run the unit tests
```

Before you push, run the one check command from the repo root: `scripts/check.sh` (hardware teammates with no Node installed use `scripts/check.sh --device`).

Backend and dashboard side:

```bash
pnpm install                             # from the repo root
pnpm db:start                            # start the local Supabase stack (needs Docker)
cd web && pnpm dev                       # start the dashboard on localhost
```

The `pnpm db:*` scripts are in the root `package.json`. `pnpm dev` is in `web/package.json`.

### Seeded events: attach clips and sensor traces

`supabase/seed.sql` creates about a dozen historical events with reports, but it cannot create files.
Until you attach them, their pages show "No clip" and "Sensor trace not available". This includes the
backup event for the pitch (spec section 7) and the event with the AI reclassification suggestion
(ARG-2026-0009).

```bash
pnpm demo:seed-media                     # from the repo root; needs local Supabase running and ffmpeg
pnpm demo:seed-media <event-id> ...      # or only the events you name
```

- It gives every seeded event that has media rows a 30 s test clip and a sensor trace. Each trace is built from
  that event's own numbers (type, peak g, closing rate, time to contact, range, sensor, free-fall time,
  drop height, environment), uses the 2 rear sensors R2 and R3, and the same detection names the device writes.
- It is safe to run again. It overwrites the same files and media rows.
- `./scripts/demo-e2e.sh --reset` runs it for you after the database reset. After a plain
  `pnpm db:reset`, run it yourself.
- Credentials come from `web/.env.local` if present, otherwise from `supabase status`.
