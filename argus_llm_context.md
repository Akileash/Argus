# Argus: Smart Hard Hat (natHacks 2026) — Full Project Context for an LLM

Paste this whole file into an LLM before asking anything about the project.

**Updated Sun Nov 8, 2026 (hardware branch).** §4 hardware table below is the **physical truth** from the Pi bring-up (DRV2605L + VL6180X + dual I2C). Older L293D/VL53L0X notes are superseded — see `docs/HARDWARE-DIFF.md`. Software cloud/dashboard status remains in §5–§11. Spec: `.plans/argus-mvp-spec.md`. Pi runbook: `docs/PI-BRINGUP.md`.

---

## 1. Event and rules

- **Hackathon:** natHacks 2026, University of Alberta (Donadeo ICE, 8th floor). Runs Fri Nov 6, 7 PM MT → Mon Nov 9, 11 AM MT.
- **Track:** Standard Division, Construction & Manufacturing.
- **Code rule:** all project code must be written during the event.
- **Submission:** public GitHub repo + README, "About the Project" (≤3,000 chars), summary (≤1,300 chars), live pitch (15 min + 5 min Q&A).
- **Hardware:** return everything undamaged by Nov 9, 7 PM MT. No soldering or modifying NAT-borrowed boards without permission (Rule 12.7.2).
- **Team:** Akileash ("aki"/Byakko), Shreay Patel, Dip Khadka, Okitha Biyanwila, Hari Mallampalli, Dhir Patel. Okitha wears the hat in the demo, and Dip Khadka is the demo safety manager.

---

## 2. What Argus is

**Argus** is a retrofit kit for a standard hard hat that works like a dash cam for construction workers.

- The hat keeps a rolling 35 s video buffer and watches its sensors.
- On a hazard it **buzzes** the wearer, saves a **30 s clip** (20 s before, 10 s after the trigger) plus the sensor data, and uploads them.
- An AI writes a **Canada-generic incident / near-miss investigation report** in OHS style. It is not a compliance filing.
- A site safety manager reviews, edits and signs off the report on a web dashboard.

**Core idea: the report is the product; the hat is the data collector.**

**One-line pitch:** "Near misses happen every day, but nobody reports them. Our hard hat catches them automatically and turns them into tomorrow's safety meeting."

**Alt pitch (market framing):** "Smart PPE is a $5B market, but it watches workers' bodies. Nobody's turning near misses into the next safety meeting. That's us."

**Wording:** say "fall **detection**", never "fall prevention". Say "this prototype covers the danger zone straight behind you". Don't claim the hard hat handles anything from above.

### Hazards detected (built)

| Hazard | How | Class |
|---|---|---|
| Struck-by from behind | 2 rear ultrasonic sensors plus the IMU (the wearer's own motion is subtracted, so walking backward into a wall doesn't trigger) | near miss; incident if contact follows |
| Fall / head impact | MPU-6050 wear-gated **fall-only** (freefall and/or hard hit); ToF must show helmet ON beforehand | incident |
| Line-of-fire / struck-by from behind | Dual HC-SR04 closing approach → haptic on matching side | near miss (Pi emits `line_of_fire` today; contract names `struck_by_*`) |
| Hat on / off (PPE context) | **VL6180X** wear band + dwell (gates falls; not a separate live `hat_removed` emit from IMU) | PPE / gate |

---

## 3. Key decisions (made by the team)

- **Compute: Raspberry Pi 4 (1 GB)**, chosen for its hardware H.264 encoder. One front camera; no rear camera (roadmap).
- **Ultrasonic: 2 rear HC-SR04** (R2, R3), aimed 7.5° left and right of straight back. No top sensor: a falling object moves too fast for it to warn in time, and the IMU still records the hit. Solid coverage ±15° behind the head; detection in ~240 ms. The full product would use 4 rear sensors.
- **Near miss vs incident:** the device decides by fixed rules. The AI may only *suggest* a reclassification, and the manager accepts or rejects it at sign-off.
- **Reports:** the AI output is always a **draft**. The manager can edit any field (the original AI draft is kept and every edit is logged), and sign-off locks the report. Corrective actions are a text list with no tracking.
- **Location:** no GPS. Every report says exactly **"Location to be determined"**, and the manager can fill it in.
- **AI:** **Gemini 3.5 Flash watches the video; Claude Sonnet 5.5 writes the report.** Chosen after a measured comparison (§7). Local models (Ollama) were tested and rejected.
- **No heartbeat:** hats only contact the backend when an event happens.
- **Clip length:** 20 s before + 10 s after (settles the earlier 60–90 s vs 10+10 s question).
- **Hosting:** fully cloud with no laptop: hat on Wi-Fi → Vercel website + Supabase. The report worker runs inside the Vercel app. Free tiers for the demo.

---

## 4. Hardware (as wired) — **Pi truth**

Verified on the Argus Pi. Code: `device/pi_bringup/`. Team plug-ins: `device/hardware/team/*/`.

| Part | Role | Connection |
|---|---|---|
| Raspberry Pi 4 | runs the hat stack | Raspberry Pi OS 64-bit; enable `dtoverlay=i2c3,pins_4_5` |
| MPU-6050 | wear-gated fall detection + motion | **I2C3** 0x68 (±16 g); sample ~**200 Hz** in bring-up |
| 2× HC-SR04 | line-of-fire / struck-by from behind | LEFT Trig **GPIO23** / Echo **GPIO24**; RIGHT Trig **GPIO20** / Echo **GPIO21** (1k/2k echo dividers) |
| **VL6180X** | hat on / off (wear gate) | **I2C3** 0x29; near **50–254 mm**; ON dwell 5 s / OFF 3 s |
| SCD41 | CO₂ / temperature / humidity | **I2C1** 0x62 |
| **2× DRV2605L** + ERM discs | haptic warning (matching US side; fall patterns) | LEFT **I2C1** 0x5A; RIGHT **I2C3** 0x5A — **not** L293D |
| Pi Camera | front rolling clip on fall | CSI / picamera2; **20 s pre + 10 s post** |

```bash
i2cdetect -y 1   # 5a LEFT DRV, 62 SCD41
i2cdetect -y 3   # 5a RIGHT DRV, 68 MPU, 29 VL6180X
```

- **Power:** USB battery bank 5 V / ≥3 A recommended for load tests.
- **Wiring safety:** never put 5 V on DRV2605L logic or a GPIO.
- **Supersedes:** earlier L293D + VL53L0X assumptions (see `docs/HARDWARE-DIFF.md`).

---

## 5. What was built (software)

```
HAT (Raspberry Pi, Python 3.11)
  sensor drivers → sample bus → detectors (struck-by, fall/impact, hat on/off)
     → event manager: buzz, cut 30 s clip + sensor trace, save to SD-card queue
     → uploader: POST event → upload clip + trace straight to cloud storage → confirm
       (keeps retrying until confirmed, so it survives Wi-Fi drops = store-and-forward)
                         │  Wi-Fi (venue Wi-Fi; phone hotspot as backup)
                         ▼
CLOUD: Vercel (Next.js dashboard + device API + report worker) + Supabase (Postgres, file storage, login, live updates)
  report worker: Gemini 3.5 Flash describes the clip → Claude Sonnet 5.5 writes the report
                 → checked against the report format → fields only the system sets are forced
                   (header, "Location to be determined", risk score, footer)
                         ▼
DASHBOARD: KPI row, live incident list + pop-up, incident page with video synced to the
           sensor chart, editable report (original kept), AI reclassification banner,
           sign-off that locks the report, print to PDF, device pairing + worker assignment
```

**Reliability built in:**
- **Sensor failures:** every sensor has backup implementations and switches automatically if one fails.
- **Bad or missing video, or the AI down:** if the video is missing or broken, Gemini is overloaded or times out, or the spend cap is reached, the report is written from the sensor data instead of failing.
- **Retries and limits:** max 3 attempts per event, a 120 s limit on video processing, and a spend cap on AI costs.
- **Teammates' code:** teammates' own tested sensor scripts can be plugged in through a short mapping file (JSON or text output) with no code changes, and hot-swapped while running.

**Repo:** `software` branch of github.com/Akileash/Argus.
- `device/`: Pi code
- `web/`: dashboard + API + worker route
- `worker/`: report pipeline
- `supabase/`: database
- `contracts/`: shared data formats
- `docs/`: guides (`CONNECT-THE-HAT.md`, `TEAM-HANDOFF.md`, `TUNING.md`, `HOSTED.md`)
- `scripts/`: `check.sh` runs before merging; `demo-e2e.sh` is the simulated demo

---

## 6. Report format (built)

The report is a Canada-generic incident / near-miss investigation:
- **Header:** report no., classification, time, site, location ("to be determined"), worker, crew, supervisor, device.
- **Narrative:** summary, sequence of events (each tagged sensor / video / inferred), evidence (clip moments + key sensor values + environment).
- **Analysis:**
  - hazard category and energy source;
  - risk: severity × likelihood on a 5×5 matrix, with the score computed by code;
  - immediate causes and 5-whys.
- **Actions and limits:**
  - corrective actions by hierarchy of controls (elimination → substitution → engineering → administrative → PPE);
  - a regulatory note ("verify with your provincial OHS regulator"; it never claims compliance);
  - confidence and gaps.
- **Sign-off** block.
- **Footer:** "AI-generated draft for review by a competent person…".

---

## 7. Measured AI results (Nov 8)

On 2 real clips (a fall from a ledge, and a first-person e-scooter crash):

| Report writer | Time | Cost/report (USD) | Verdict |
|---|---|---|---|
| Gemini 3.5 Flash alone | ~25 s | ~$0.03 | states guesses as facts; few actions |
| **Gemini + Claude Sonnet 5.5** ✅ | ~40 s | ~$0.06 | careful, separates known from unknown, practical actions |
| Gemini + Claude Opus 5.5 | ~50 s | ~$0.10 | best judgement, most expensive |

- **Gemini model:** use `gemini-3.5-flash`, which answered correctly in about 3 s. `gemini-3.8-flash` was overloaded (90–117 s or 503 errors).
- **Local models:** Qwen3-VL, Gemma 3 and Qwen2.5-VL on a laptop took 80–130 s, misread the fall and invented details.
- **End-to-end timing:** an event reaches the dashboard about 11 s after the trigger (the hat records the 10 s post-roll first). The report is ready about 40–60 s later.

---

## 8. Demo and pitch

**Pitch flow (15 min):** hook stats → problem → introduce Argus as "just" a smart hard hat → live demo → "one more thing" dashboard reveal → market → business model → competition → roadmap → honest limits.

**Live demo (no laptop):**
1. Okitha wears the hat on venue Wi-Fi (phone hotspot as backup). A QR code on a slide opens the dashboard on the judges' phones with a read-only judge login.
2. **Struck-by:** a teammate swings a tested object at the hat from straight behind (inside the ±15° zone; mark the spot and rehearse). The back motor buzzes; the wearer says so.
3. **Fall / impact:** drop the hat onto a mat. It logs one fall event with peak g.
4. About 11 s later the incident appears on everyone's phone with the 30 s clip. The report follows about 40–60 s later. While waiting, narrate it.
5. Open the incident: the video is synced to the sensor chart, plus the report and the AI reclassification banner. Edit, accept, sign off, then print to PDF.
- **Backups:**
  - **Simulated demo:** `./scripts/demo-e2e.sh --keep-running` runs the whole system on a laptop. Its fall and scooter-crash events use real video.
  - **Stage trigger:** `argus-cli trigger` fires an event if the sensors misbehave.
- **Closing line:** *"There's no laptop. The hat, Wi-Fi and the cloud. That's the whole system."*

**Honest limits:**
- Rear coverage is ±15° (production adds sensors), and falling objects get no advance warning.
- Expect false positives; the cooldowns and the human review step cover them.
- The fall clip is from a security camera; the scooter clip is first-person.
- Battery life is demo-grade.
- Nothing has been tuned on the real hat yet.

---

## 9. Connectivity story

- **Primary:** the hat uploads directly to the cloud over Wi-Fi.
- **No signal:** the hat stores events on its SD card and uploads when it reconnects (built and tested).
- **Real site:** one cellular Wi-Fi router per site (not per hat), and the hat's own queue covers dead zones.
- **Roadmap:** urgent alerts over low-bandwidth radio (LoRa) as tiny packets, never video. Don't pitch CAN or I2C as a radio fallback.

---

## 10. Problem, market and competition

**Problem:** sites learn from injuries, not from the close calls before them. Near misses mostly go unreported because reporting is manual, awkward and can feel like snitching, so safety meetings are generic.
- Safety pyramid (~300 near misses per major injury, IHSA): treat it as an idea, not a hard stat.
- Hook stats (re-verify before use): Canada 183 construction deaths in 2022 (AWCBC); US 1,075 in 2023 (BLS).

**Market (global, 2025, USD, from Shreay's research):**
- Smart PPE ≈ $4.9B → ≈ $10.2B by 2031 (ResearchAndMarkets).
- Construction wearables ≈ $4.6B (EHS Today).
- Smart PPE for construction ≈ $623M → ≈ $1.9B by 2033 (Grand View Research).
- Beachhead: Canada, ~1.65M construction workers (StatCan), plus Alberta oil & gas.

**Business model:** $300 CAD per hat per year (per-hat subscription; free pilot → paid). Buyers are safety managers at general contractors and subcontractors.
- **Team's model:** hardware $77 + support $24 + replacements $23 + cloud/AI $12 = $136 cost, $164 kept (54.7% gross margin). The arithmetic checks out.
- **Review of that model:** hardware is likely underestimated. Parts are about $215–305 CAD at retail; dropping the SCD41 and buying in volume helps. It also misses connectivity, card fees (~3%) and batteries. A realistic gross margin is ~35–45% (~22% with per-hat cellular). One cellular router per site keeps connectivity cheap. Call it **gross** margin in the pitch.

**Competition:** SenseHuman AI (Edmonton) makes a 4-camera 360° smart hard hat with real-time directional vibration, piloted with The Bartlett Group. Differentiate on the report and learning loop, cost, simplicity and retrofit. Verify claims about what they lack.

---

## 11. Status (Nov 8)

- **Done on the real Pi (`hardware` branch / `device/pi_bringup/`):**
  - MPU + dual US + dual **DRV2605L** + **VL6180X** wear gate + SCD41 + Pi Camera path;
  - wear-gated fall-only + all-sensors load test; sample JSON for teammates;
  - team vendor stubs under `device/hardware/team/{imu,range,hat_on,env,haptics,camera}/`.
- **Done and verified on a laptop with simulated sensors (software stack):**
  - the full chain hat → upload → AI report → dashboard → sign-off → PDF;
  - fallbacks and hot-swapping; teammate script integration framework;
  - real Gemini and Claude reports on real clips.
- **In progress:**
  - hosting/deploy/pairing; map Pi event JSON → `contracts/event.schema.json` (`docs/CONTRACT-BRIDGE.md`);
  - finish camera `save_command` wiring to ClipRecorder; threshold tune on recordings.
- **Still open:** hosted end-to-end with the physical hat on venue Wi-Fi.

---

## 12. From the earlier plan, not built (roadmap)

- Weekly / end-of-shift report and the "mission" for the next toolbox talk. Each report has corrective actions, but there's no weekly roll-up yet.
- Zones (AprilTag/QR markers) and zone heat maps.
- ~~DRV2605L haptic driver~~ → **built on the Pi (this branch); L293D path is obsolete for our hat.**
- Front-facing ultrasonic. The build uses 2 rear sensors.
- FastAPI + SQLite backend. The build uses Next.js + Supabase.
- Radio alerts, rear/360 camera, better computer vision, fatigue detection, EEG.

---

## 13. Rules for anyone working on this

- Never put API keys in code, chat or the public repo. They live in `.env` (git-ignored) and in Vercel/Supabase settings.
- Don't change `contracts/` without telling the team.
- Run `scripts/check.sh` before merging.
- Teammates plug in sensor code only under `device/hardware/team/<sensor>/` (see `docs/TEAM-HANDOFF.md`).
