# Argus

A retrofit kit that turns a standard hard hat into a dash cam for construction workers.

- **Senses danger:** two rear ultrasonic sensors watch for objects closing in from behind, an IMU detects falls, and a time-of-flight sensor knows when the hat comes off.
- **Warns the wearer:** left and right vibration motors buzz on the side the hazard is coming from, before it reaches them.
- **Records what happened:** the hat keeps a rolling video buffer and saves a clip (20 s before, 10 s after) for every fall, and uploads the sensor data for every event.
- **Writes the report:** an AI drafts a Canada-generic incident / near-miss investigation report, which a site safety manager reviews, edits and signs off in a web dashboard.

> **Status:** running end to end on a real hat: sensors, haptics, camera, cloud upload, AI reports and the dashboard. Detection thresholds are tuned from real test sessions and are still prototype-grade. See [What's proven and what isn't](#whats-proven-and-what-isnt).

## How it works

```
 Hard hat (Raspberry Pi 4)                               Cloud
 ┌───────────────────────────────────┐                   ┌──────────────────────────────┐
 │ hat.main: sensors, detection,     │                   │ Next.js dashboard + API      │
 │ haptic buzz, video clips          │   event.json      │ (Vercel)                     │
 │        │ events + clips           │   clip.mp4   ───► │ Supabase (database, files)   │
 │        ▼                          │   trace.json      │ report worker (Gemini/Claude)│
 │ cloud bridge: pairing, mapping,   │                   └──────────────────────────────┘
 │ on-disk queue, retrying upload    │
 └───────────────────────────────────┘
```

1. `hat.main` (`device/pi_bringup/hat/`) owns every sensor. It detects near misses and falls, buzzes the motors and saves video clips.
2. The cloud bridge (`device/argus/bridge/`) runs `hat.main` as a child process and reads its events, clips and hat-on/off lines. It never opens a sensor itself.
3. The bridge converts each event to the shared format in `contracts/` and hands it to the upload queue, which keeps retrying until it gets through, so a Wi-Fi drop loses nothing.
4. The report worker drafts the report. A near miss within 6 s before a fall is linked to it, and the report names it as the most likely cause.
5. The dashboard shows the incident live, with the video and sensor chart. A safety manager edits and signs off the report.

**Detection rules (hat):**
- **Near miss:** an object closing on a rear sensor, confirmed over several readings, at 1 m/s or faster (2 m/s while the wearer is moving), or a very fast approach. Fast head turns are ignored.
- **Fall:** the hat must be worn. Then either free fall of 100 ms or more with a 2 g peak and at least 30° of head tilt, or a hit of 2.5 g or more with at least 45° of tilt, followed by lying still. Readings above 15 g are treated as bad data.
- **Hat off:** the time-of-flight sensor reads outside 50 to 254 mm for 3 s.

## Hardware

| Part | Job | Connection |
|---|---|---|
| Raspberry Pi 4 | runs everything on the hat | Raspberry Pi OS |
| Pololu MinIMU-9 v3 (LSM303D accel, L3GD20H gyro) | falls, head motion | I2C bus 3, `0x1D` and `0x6B` |
| 2× HC-SR04 | objects approaching from behind | LEFT trig 23 / echo 24, RIGHT trig 20 / echo 21 (GPIO), with echo dividers |
| VL6180X | hat on / off | I2C bus 3, `0x29` |
| SCD41 | CO₂, temperature, humidity (context only) | I2C bus 1, `0x62` |
| 2× DRV2605L + vibration motors | left and right haptic warning | `0x5A`, LEFT on I2C bus 1, RIGHT on I2C bus 3 |
| Pi Camera (CSI) | video clips | `picamera2` + `ffmpeg` |

I2C bus 3 needs `dtoverlay=i2c3,pins_4_5` in `/boot/firmware/config.txt` (SDA GPIO4, SCL GPIO5). Wiring details: [device/pi_bringup/Argus_Hat_Wiring.md](device/pi_bringup/Argus_Hat_Wiring.md). The earlier MPU-6050 driver is archived in `device/pi_bringup/hat/archive/`.

## Repo layout

| Folder | What's in it |
|---|---|
| `device/pi_bringup/` | The code that runs the real hat: sensors, detection, haptics, camera, tests and tools |
| `device/argus/bridge/` | Cloud bridge between the hat code and the backend |
| `device/` (rest) | Upload queue, pairing, `argus-cli`, simulated sensors for laptop demos |
| `web/` | Next.js dashboard, device API and report worker route |
| `worker/` | Report pipeline (Gemini and/or Claude) |
| `supabase/` | Database schema, security rules, seed data and SQL tests |
| `contracts/` | The data formats shared by the hat, the API and the dashboard |
| `packages/shared/` | Shared TypeScript types |
| `scripts/` | Demo, check and helper scripts |
| `docs/` | Guides for the team |
| `.plans/` | Product spec, build plan and bridge spec |

## Quick start on a laptop (simulated hat)

Needs macOS with [Homebrew](https://brew.sh), then `brew install uv node@22 pnpm ffmpeg` and **Docker Desktop** running.

```bash
git clone -b software https://github.com/Akileash/Argus.git
cd Argus
pnpm install
(cd device && uv sync)
./scripts/demo-e2e.sh --reset --keep-running
```

This starts the database, builds the dashboard, runs a simulated hat through three demo events (a fall and a scooter crash, both with real video, and a hat removal) and checks the results (about 3 minutes). When it prints `RESULT: PASS`, open **http://127.0.0.1:3400** and log in with `demo@argus.local` / `argus-demo-2026`. Press Ctrl+C to stop.

> `--reset` wipes the local database and reloads the demo data. Leave it out to keep your data.

## Running on the real hat

The hat code lives in `~/argus` on the Pi (a copy of `device/pi_bringup/`), and the bridge in its own folder, `~/argus-cloud`.

```bash
# Install the bridge (needs the repo at ~/argus-cloud)
~/argus-cloud/device/deploy/install-bridge.sh

# Register the hat on the dashboard's Devices page, then pair it with the command shown there
cd ~/argus-cloud/device && .venv/bin/argus-cli pair --config /etc/argus/config.yaml

# Start it (keep the hat still for a few seconds while the gyro calibrates)
sudo systemctl start argus-bridge
```

The bridge starts `hat.main` and the air sensor for you, so don't also start `hat.main` by hand or enable `argus-sensors.service`: only one program can own the sensors. Logs: `journalctl -u argus-bridge -f`.

**Live view** of every sensor, the motors and the fall detector, while the hat runs:

```bash
cd ~/argus && .venv/bin/python -m hat.tools.live_view
```

**Test a sensor** with the bridge stopped: `python -m hat.tools.test_imu check`. Full sensor bring-up notes: [docs/PI-BRINGUP.md](docs/PI-BRINGUP.md). Hosting setup: [docs/HOSTED.md](docs/HOSTED.md).

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
| `cd device && uv run argus-cli --help` | Device commands: `bridge`, `pair`, `upload`, `run`, `test`, `trigger`, `team` … |
| `cd device/pi_bringup && python -m pytest hat/tests` | Tests for the real hat code |

## What's proven and what isn't

**Proven on the real hat:** sensors, haptics and camera working together; events uploading to the hosted backend; AI reports on real events; hat-on/off detection; automatic recovery when the motion sensor loses its settings.

**Proven on a laptop:** the full simulated demo, sign-off and PDF export, the bridge's retry and mapping logic, and all automated tests (`scripts/check.sh`).

**Known limits:**
- Detection thresholds were tuned from a small number of test falls and near misses, so expect some missed events and false alarms.
- The motion sensor ignores data while the motors buzz, about 1.6 s after a near miss on both sides.
- A hard landing can press the hat close enough to the time-of-flight sensor to report it as off.
- Rear coverage is limited to the two sensors' beams, and the hat has no contact sensor, so a linked near miss is reported as the *likely* cause of a fall, not a confirmed strike.
- The prototype's wiring can drop out under hard knocks; production hardware needs soldered, strain-relieved connections.

## More documentation

- [docs/README.md](docs/README.md): index of all guides and references
- [.plans/argus-mvp-spec.md](.plans/argus-mvp-spec.md): the product spec, including the pitch demo runbook (§7)
- [.plans/argus-build-plan-spec.md](.plans/argus-build-plan-spec.md): build plan and current status
