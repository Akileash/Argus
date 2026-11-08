# Argus: project context (for another LLM)

Updated 2026-11-08. This is a summary of the whole project as it stands. Authoritative details are in the repo files named below.

## 1. What Argus is

Argus is a retrofit kit for a standard hard hat that works like a dash cam for construction workers. Built as a startup MVP for a live pitch (natHacks, Edmonton, AB).

- The hat keeps a rolling 35 s video buffer and watches its sensors.
- On a hazard it buzzes the wearer, saves a 30 s clip (20 s before, 10 s after the trigger) plus sensor data, and uploads it.
- An AI writes a Canada-generic incident / near-miss investigation report (OHS style, not a compliance filing).
- A site safety manager reviews, edits and signs off the report on a web dashboard.

**Hazards detected:**

| Event | How | Class |
|---|---|---|
| Struck-by from behind | 2 rear ultrasonic sensors plus IMU (wearer's own motion subtracted) | near miss; incident if contact |
| Fall / head impact | MPU-6050 IMU (free fall then impact; ≥6 g impact) | incident |
| Hat knocked off | ToF sensor shows hat off within 2 s after an impact | incident |
| Hat removed | ToF shows hat off, no impact | PPE log (no clip, no report) |

## 2. Key decisions (all made by the team)

- **Ultrasonic sensors:** 2 rear HC-SR04 (R2, R3), aimed 7.5° left and right of straight back. No top sensor. Solid coverage is ±15° behind the head; detection takes ~240 ms (60 ms sweep, 3 readings). Chosen for MVP reliability and simplicity (spec §3.2a). A product would use 4 rear sensors.
- **Near miss vs incident:** the device decides by fixed rules. The AI can only *suggest* a reclassification; the manager accepts or rejects it at sign-off.
- **Reports:** the AI output is always a DRAFT. The manager can edit any field (the original AI draft is kept and edits are logged), and sign-off locks the report. No corrective-action tracking (actions are a text list).
- **Location:** no GPS. Every report says exactly "Location to be determined"; the manager can fill it in.
- **AI:** Gemini 3.5 Flash watches the video. **Claude Sonnet 5.5 writes the report** (`REPORT_MODE=hybrid`, chosen after a measured comparison; see §6). Local Ollama was tested and rejected.
- **No heartbeat:** hats only talk to the backend when an event happens.
- **Hosting:** fully cloud, no laptop. Hats on Wi-Fi upload to a Vercel website plus Supabase. The report worker runs inside the Vercel app. All on free tiers for the demo.
- **Demo:** two scenarios with real video (a security-camera fall from a ledge, and a first-person e-scooter crash). The struck-by demo is off by default.

## 3. Hardware (spec §2)

Raspberry Pi 4 (1 GB) running Raspberry Pi OS Bookworm 64-bit Lite.

| Part | Role | Connection |
|---|---|---|
| MPU-6050 | falls, impacts, head motion | I2C 0x68, ±16 g, 1 kHz FIFO, I2C at 400 kHz |
| 2× HC-SR04 | struck-by from behind | R2 GPIO19/26, R3 GPIO23/24 (trig/echo), 1 kΩ/2 kΩ echo dividers on one soldered hub board |
| VL53L0X | hat on/off | I2C 0x29 |
| SCD41 | CO₂ / temp / humidity (context only) | I2C 0x62 |
| L293D + 2 vibration motors | haptic warning (back motor = struck-by; both = fall/impact) | GPIO12 (top), GPIO13 (back), software PWM via lgpio |
| Pi Camera V2 | front video, H.264 | CSI |
| Status LED (optional) | calibration cues | GPIO17 |

Power: Anker 5,000 mAh (5 V, 3 A). Worst case about 1.45–1.75 A.

Boot: the hat rests on a table while it calibrates, then the LED blinks to say "put it on".

## 4. Repo layout (`software` branch of github.com/Akileash/Argus)

| Path | What |
|---|---|
| `device/` | Python 3.11 code for the Pi (uv project): `argus/` core (supervisor, detectors, events, uploader, pairing, `argus-cli`), `hardware/argus/` real drivers, `hardware/team/` slots for teammates' own sensor scripts, `hardware/mock/` simulated sensors, `scenarios/` demo scenarios, `fixtures/clips/` the 2 real demo videos, `install.sh`, `config.yaml` |
| `web/` | Next.js 15 dashboard + device API (`/api/devices/*`, `/api/events/*`) + report worker route (`/api/worker/run`) |
| `worker/` | Report worker library (Gemini/Claude pipeline), also runnable standalone |
| `supabase/` | Migrations, RLS, demo seed, SQL tests; `hosted/` has the production setup data and cron SQL |
| `contracts/` | Data formats shared by hat, API and dashboard (event, sensor trace, report JSON Schemas, API, device queue) |
| `packages/shared/` | Shared TypeScript types |
| `scripts/` | `check.sh` (run before merging), `demo-e2e.sh` (full simulated demo), `hosted/` (deploy helpers), `llm-bench/` (AI comparisons) |
| `docs/` | `CONNECT-THE-HAT.md`, `TEAM-HANDOFF.md`, `TUNING.md`, `HOSTED.md`, `README.md` |
| `.plans/` | `argus-mvp-spec.md` (the spec), `argus-build-plan-spec.md` (build plan + status), `argus-sensor-layout.html` |

**Modular / hot-swappable:** every sensor module has 4 implementations: `argus` (our driver), `team` (a teammate's script via a `mapping.yaml`, JSON or text output), `mock`, and `replay`. They are picked in `config.yaml` with automatic fallback if one fails. `argus-cli reload <module>` swaps them live.

## 5. How data flows

1. Pi drivers → sample bus → detectors → event manager (buzz, cut clip + trace, write to the on-disk queue).
2. The uploader POSTs `event.json` to `/api/events`, uploads the clip and trace straight to Supabase Storage via signed URLs, then calls `/complete`. It retries until confirmed, so outages are survived.
3. The web app starts the report in the background (`after()`). A once-a-minute Supabase cron job calls `/api/worker/run` to catch stragglers.
4. The worker runs Gemini (video) → Claude Sonnet (writer). The report is validated against the schema, and fields the worker owns (header, location, risk score, footer) are forced.
5. Fallbacks:
   - no usable video, Gemini overloaded or timed out → report from sensor data;
   - spend cap reached → free sensor-only draft;
   - max 3 attempts per event.
6. The dashboard (Supabase Realtime) shows a live list, a pop-up, the incident page with video synced to the sensor chart, editing, sign-off, and print to PDF.

## 6. Measured AI results (2026-11-08)

| Report writer (same 2 real clips) | Time | Cost/report (USD) | Verdict |
|---|---|---|---|
| Gemini 3.5 Flash alone | ~25 s | ~$0.03 | states guesses as facts, few actions |
| Gemini + Claude Sonnet 5.5 ✅ | ~40 s | ~$0.06 | careful, practical, separates known from unknown |
| Gemini + Claude Opus 5.5 | ~50 s | ~$0.10 | best judgement, most expensive |

- Gemini model: `gemini-3.5-flash`. Google currently serves it as 3.6 Flash. `gemini-3.8-flash` was overloaded (90–117 s or 503 errors).
- Local Ollama (Qwen3-VL 8B, Gemma 3 12B, Qwen2.5-VL 7B) on an M4 Pro: 80–130 s per report, misread the fall, invented details. Rejected. Details: `scripts/llm-bench/`.

## 7. Hosting status (in progress)

- Supabase cloud project `qlbudlqvzajtxsbogjyu`: schema applied; real setup data loaded (org Argus, site natHacks Edmonton AB, crew "natHacks Crew" supervised by Dip Khadka, worker Okitha Biyanwila on hat ARG-0001). No demo data.
- Vercel project `argus` (team account dippyboy123): created and linked; deployed from the CLI (not GitHub).
- Remaining steps (see `docs/HOSTED.md`):
  1. add env vars to Vercel;
  2. confirm Fluid Compute is on (needed for 300 s functions);
  3. deploy;
  4. turn on the cron safety net;
  5. turn off public sign-up in Supabase;
  6. create the manager login (Dip Khadka) and a read-only judge login with `scripts/hosted/create-user.ts`;
  7. register and pair hat ARG-0001 with the Vercel URL.
- Venue Wi-Fi is good; the Pi is already on it. A phone hotspot is the backup.
- Backup demo: everything runs locally on the laptop (`./scripts/demo-e2e.sh --keep-running`, needs Docker).

## 8. Verified vs not yet proven

- **Verified on a laptop with simulated sensors:**
  - the full chain hat → upload → AI report → dashboard → sign-off → PDF;
  - hot-swapping and fallbacks;
  - teammate script integration;
  - 5 rounds of independent review and testing;
  - `scripts/check.sh` all pass;
  - real Gemini + Claude reports on the 2 real clips.
- **Not yet proven:**
  - the real drivers on the real Pi (bench checks in spec §3.2a: beam width, demo object echo, the "no echo" pulse, motor noise, head-down posture);
  - detection thresholds tuned on real recordings (`argus-cli record` / `sweep` / `eval`);
  - the hosted deployment end to end.

## 9. Business model being pitched

$300 CAD per hat per year.

- **Team's estimate:** hardware $77, support $24, replacement $23, cloud/AI $12, giving $164 kept (54.7% gross margin). The arithmetic is correct.
- **Review findings:** hardware is likely underestimated (parts ≈ $215–305 CAD at retail; dropping the SCD41 plus volume pricing helps). Missing costs are connectivity, payment fees (~3%) and batteries. A realistic gross margin is ~35–45%, or ~22% with per-hat cellular.
- **Real-site plan:** one cellular Wi-Fi router per site, not per hat, and the hat's own upload queue covers outages.

## 10. Rules for anyone working on this

- Never put API keys in code, chat or git. They live in the root `.env` (git-ignored) and in Vercel/Supabase settings.
- Don't change `contracts/` or `device/argus/contracts.py` without telling the team.
- Run `scripts/check.sh` before merging.
- Teammates plug in sensor code only under `device/hardware/team/<sensor>/` (see `docs/TEAM-HANDOFF.md`).
- The final GitHub push should exclude demo-only files (demo script, scenarios, demo videos, demo seed).
