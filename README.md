# Argus

A retrofit kit that turns a standard hard hat into a dash cam for construction workers.

- **Senses danger:** two rear ultrasonic sensors watch for objects closing in from behind, an IMU detects falls and head impacts, and a time-of-flight sensor knows when the hat comes off.
- **Warns the wearer:** vibration motors buzz on the back of the head before a struck-by hazard reaches them.
- **Records what happened:** the hat keeps a rolling video buffer and saves a clip (20 s before, 10 s after) plus the sensor data for every incident or near miss.
- **Writes the report:** an AI drafts a Canada-generic incident / near-miss investigation report, which a site safety manager reviews, edits and signs off in a web dashboard.

> **Status:** built and verified end to end with simulated sensors. The real hardware drivers exist but have not yet run on a real Pi. See [What's proven and what isn't](#whats-proven-and-what-isnt).

## How it works

```
 Hard hat (Raspberry Pi 4)                         Laptop or cloud
 ┌───────────────────────────────┐                 ┌─────────────────────────────┐
 │ sensors → drivers → detectors │   event.json    │ Next.js dashboard + API     │
 │ → haptic buzz                 │  clip.mp4  ───► │ Supabase (database, files)  │
 │ → clip + trace → upload queue │  trace.json     │ report worker (Gemini/Claude)│
 └───────────────────────────────┘                 └─────────────────────────────┘
```

1. Each sensor driver runs in its own thread and turns raw readings into standard units (mm, g, deg/s).
2. A supervisor watches every sensor's health and can hot-swap a sensor's code, or fall back to a backup, while the hat runs.
3. Detectors decide what happened: struck-by (with the wearer's own motion subtracted), fall, head impact, hat knocked off or removed.
4. The event manager buzzes the motors, cuts the clip and sensor trace, and queues the event on disk.
5. The uploader sends it to the backend and keeps retrying until it gets through.
6. The report worker drafts the report. The dashboard shows it live, with the video synced to the sensor chart.

## Hardware

| Part | Job | Driver |
|---|---|---|
| Raspberry Pi 4 | runs everything on the hat | — |
| MPU-6050 (I2C 0x68) | falls, impacts, head motion | `smbus2`, 1 kHz FIFO |
| 2× HC-SR04 (R2, R3) | objects approaching from behind | `lgpio` pulse timing |
| VL53L0X (I2C 0x29) | hat on / off | Adafruit library |
| SCD41 (I2C 0x62) | CO₂, temperature, humidity (context only) | Adafruit library |
| L293D + 2 vibration motors | haptic warning | `lgpio` PWM on GPIO12/13 |
| Pi Camera (front) | video clips | `picamera2` + `ffmpeg` |

Wiring and the full GPIO map: [docs/CONNECT-THE-HAT.md](docs/CONNECT-THE-HAT.md) and spec §2.3.

## Repo layout

| Folder | What's in it |
|---|---|
| `device/` | Python code for the Pi: drivers, supervisor, detectors, event manager, uploader, `argus-cli`, install script |
| `device/hardware/team/` | One folder per sensor where teammates plug in their own tested scripts |
| `web/` | Next.js dashboard and the device API |
| `worker/` | Report worker that drafts reports with Gemini and/or Claude |
| `supabase/` | Database schema, security rules, demo seed data and SQL tests |
| `contracts/` | The data formats shared by the hat, the API and the dashboard |
| `packages/shared/` | Shared TypeScript types |
| `scripts/` | Demo, check and helper scripts |
| `docs/` | Guides for the team |
| `.plans/` | The product spec and build plan |

## Quick start on a laptop (simulated hat)

Needs macOS with [Homebrew](https://brew.sh), then `brew install uv node@22 pnpm ffmpeg` and **Docker Desktop** running.

```bash
git clone -b software https://github.com/Akileash/Argus.git
cd Argus
pnpm install
(cd device && uv sync)
./scripts/demo-e2e.sh --reset --keep-running
```

This starts the database, builds the dashboard, runs a simulated hat through two demo events (a fall, using a real fall video, and a hat removal) and checks the results (about 3 minutes). When it prints `RESULT: PASS`, open **http://127.0.0.1:3400** and log in with `demo@argus.local` / `argus-demo-2026`. Press Ctrl+C to stop.

> `--reset` wipes the local database and reloads the demo data. Leave it out to keep your data.

## Running on the real hat

Step-by-step guide, from a blank SD card to your first real report: **[docs/CONNECT-THE-HAT.md](docs/CONNECT-THE-HAT.md)**. In short:

1. Flash Raspberry Pi OS Bookworm 64-bit Lite, then on the Pi:
   ```bash
   git clone -b software https://github.com/Akileash/Argus.git ~/argus
   cd ~/argus/device && ./install.sh && sudo reboot
   ```
2. In `/etc/argus/config.yaml`, switch each sensor from `impl: mock` to `impl: argus`, then test each one:
   ```bash
   .venv/bin/argus-cli test imu --config /etc/argus/config.yaml
   ```
3. Register the hat on the dashboard's **Devices** page and run the pairing command it shows on the Pi.
4. `sudo systemctl start argus` with the hat resting on a table, then put it on.

The dashboard must be reachable from the Pi: use your laptop's network IP, not `127.0.0.1` (see the guide, step e0).

## Plugging in teammates' sensor code

Teammates' tested scripts plug in without changing Argus code: copy the script into `device/hardware/team/<sensor>/vendor/`, describe its output in a `mapping.yaml`, and check it with pasted sample output:

```bash
cd device
uv run argus-cli team init range
uv run argus-cli team check range --sample sample.txt
```

Full guide: [docs/TEAM-HANDOFF.md](docs/TEAM-HANDOFF.md). Team rules (folders, branches, the check before merging): [CONTRIBUTING.md](CONTRIBUTING.md).

## AI reports and API keys

Put keys in a `.env` file at the repo root. It is git-ignored, so keys never get committed. See [.env.example](.env.example).

| Setting | Meaning |
|---|---|
| `REPORT_MODE` | `fixture` (canned report, no keys), `gemini` (Gemini watches the clip and writes the report), or `hybrid` (Gemini describes the clip, Claude writes the report) |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | Google AI Studio key and model name |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` | Anthropic key and model (hybrid mode) |
| `LLM_SPEND_CAP_USD` | stops paid calls past this total; events then get a sensor-only draft |
| `GEMINI_PROCESSING_TIMEOUT_S` | how long to wait for Gemini to process a clip (default 120) |

Events without usable video get a report written from sensor data alone. All worker settings: [docs/README.md](docs/README.md#report-worker-settings).

## Useful commands

| Command | What it does |
|---|---|
| `scripts/check.sh` | Run every test and check. Run it before merging. |
| `./scripts/demo-e2e.sh --reset --keep-running` | Full simulated demo, dashboard left running |
| `scripts/web-e2e.sh` | Browser tests against a running dashboard |
| `cd device && uv run argus-cli --help` | All hat commands: `run`, `test`, `buzz`, `pair`, `upload`, `record`, `sweep`, `eval`, `trigger`, `reload`, `team` … |
| `uv run argus-cli record --label <name>` / `argus-cli sweep` | Record real trial runs and tune detection thresholds ([docs/TUNING.md](docs/TUNING.md)) |

## What's proven and what isn't

**Proven on a laptop with simulated sensors:** the whole chain from hat to signed report, live dashboard updates, sign-off and PDF export, hot-swapping sensor code, fallbacks, and teammate script integration (`scripts/check.sh`, the demo and the browser tests all pass).

**Not yet proven:**
- The real hardware drivers on a real Pi (bench checks are in spec §3.2a).
- Detection thresholds tuned on real recordings.
- Gemini and Claude reports on real clips.

## More documentation

- [docs/README.md](docs/README.md): index of all guides and references
- [.plans/argus-mvp-spec.md](.plans/argus-mvp-spec.md): the product spec, including the pitch demo runbook (§7)
- [.plans/argus-build-plan-spec.md](.plans/argus-build-plan-spec.md): build plan and current status
