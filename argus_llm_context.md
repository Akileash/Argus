# Argus: Smart Hard Hat (natHacks 2026) — Full Project Context for an LLM

Paste this whole file into an LLM before asking anything about the project. It merges everything the team has discussed so far (as of Fri Nov 6, 2026, before hacking starts). Where teammates' notes disagree, both versions are listed and marked as undecided.

---

## 1. Event and rules

- **Hackathon:** natHacks 2026, University of Alberta (Donadeo ICE, 8th floor). Runs Fri Nov 6, 7 PM MT → Mon Nov 9, 11 AM MT.
- **Track:** Standard Division, Construction & Manufacturing.
- **Code rule:** all project code must be written during the event. Code written before 7 PM Friday counts as past material and can get us disqualified. Before then, only wiring tests, CAD, research and pitch outlines. Nothing committed to the repo early.
- **Submission:** public GitHub repo + README, "About the Project" (≤3,000 chars), summary (≤1,300 chars), live pitch (15 min + 5 min Q&A).
- **Hardware:** pickup Nov 6, 2–5 PM MT; return everything undamaged by Nov 9, 7 PM MT. No soldering or modifying NAT-borrowed boards without permission (Rule 12.7.2), so ask NAT on Discord / team@nat.ltd.
- **Team (5–6):** Akileash ("aki"/Byakko), Shreay Patel, Dip Khadka, Okitha Biyanwila, Hari Mallampalli, Dhir Patel. Captain and role owners not confirmed yet.
- **Suggested roles:** hardware & power; hat software; backend + LLM; frontend; CAD/mounting; business & pitch.

---

## 2. What we're building

**Argus** is a retrofit kit for a standard hard hat that works like a dash cam for construction workers.

- A camera keeps a rolling buffer of video.
- Sensors detect **line-of-fire / struck-by** events (something approaching fast) and **falls / head impacts**.
- Line-of-fire events make the hat **vibrate** to warn the wearer right away.
- Every incident or near miss saves a video clip plus sensor data and sends it to a backend.
- An **LLM turns each event into a safety report** (what happened, contributing hazards, what to watch for, risk-matrix rating).
- Reports roll up into a **weekly / end-of-shift report** and a **"mission" for the next toolbox/safety meeting**.

**Core idea: the report is the product; the hat is the data collector.**

**One-line pitch:** "Near misses happen every day, but nobody reports them. Our hard hat catches them automatically and turns them into tomorrow's safety meeting."

**Alt pitch (market framing):** "Smart PPE is a $5B market, but it watches workers' bodies. Nobody's turning near misses into the next safety meeting. That's us."

### Hazards targeted

| Hazard | How Argus handles it | Sensor |
|---|---|---|
| Line of fire / struck-by | Real-time haptic warning + logged event + clip | HC-SR04 ultrasonic (front) |
| Falls / head impacts | Logged event + clip + dashboard alert | MPU-6050 accel/gyro |
| Hat knocked off / removed | Logged event | IMU spike (+ optional "hat worn" sensor) |

**Wording:** say "fall **detection**", never "fall prevention". The hat only helps prevent falls indirectly, through the report loop.

### Scope decisions
- **Cut:** live audio transcription (scope creep + privacy).
- **Demoted:** temperature/humidity. Keep only if pitched as heat-stress warnings; otherwise drop.
- **Easy add-on:** works offline, syncs when Wi-Fi returns (store-and-forward), which ties to the remote-site angle.

---

## 3. Hardware

### Parts available (team-owned + NAT kit)
- **Compute:** Raspberry Pi 5 (4GB) + official active cooler + 27W USB-C PSU (Akileash's); Raspberry Pi 4 (1GB) (NAT kit); ESP32; Arduino Uno R3.
- **Sensors:** MPU-6050 accel/gyro; HC-SR04 ultrasonic (in plan); camera / USB webcam.
- **Haptics:** vibrating disc motors + driver board (DRV2605L), requestable from NAT (FAQ page 10 lists requestable hardware).
- **Power:** 300W 20A adjustable buck converter; Zeee 11.1V 3S 2200mAh 50C LiPo (×2); 1–8S LiPo voltage tester/low-voltage alarm; 3.7V Li-Po cells (requestable); USB-C power bank (plan).
- **Other:** 4× TT gear motors + L298N driver (from a robot kit, probably unused), LEDs, rocker switches, Dupont jumpers, 20 AWG silicone wire, XT60 pigtails, 16A terminal block, spade/crimp kit, M2–M5 screw kit.

### Open hardware questions
- **Pi 4 or Pi 5?** Pi 4 has a hardware H.264 encoder (good for continuous recording); Pi 5 encodes in software but has more CPU headroom. Pi 5 wants 5V/5A (3A works with USB limits); Pi 4 needs 5V/3A.
- **One or two cameras?** Default one. A rear camera is a stretch goal (doubles encode load and webcam power).
- Where each part mounts without blocking vision or unbalancing the hat (plan: Pi + battery enclosure on the earmuff mounts, sensor/webcam on the brim).

### Wiring plan
| Part | Connection |
|---|---|
| HC-SR04 | VCC → 5V (pin 2), GND → pin 9, Trig → GPIO23 (pin 16), Echo → 1 kΩ → junction → GPIO24 (pin 18); junction → 2 kΩ → GND (divider: Echo is 5V, Pi GPIO is 3.3V) |
| MPU-6050 (0x68) + DRV2605L (0x5A) | Shared I2C: 3.3V pin 1, GND pin 6, SDA GPIO2 pin 3, SCL GPIO3 pin 5 |
| Webcam | USB |
| Power | USB-C power bank; test under load and check `vcgencmd get_throttled` |

- DRV2605L has a fixed address (0x5A): a 2nd board needs a second I2C bus (dtoverlay), an I2C mux, or drive motor #2 with a transistor from GPIO.
- Rough power budget: Pi 4 ~3–6 W, webcam ~0.5–1.5 W → ~5–8 W total; a 10,000 mAh bank lasts a few hours. Fine for a demo; say so honestly for a full shift.

---

## 4. Detection logic (first pass, tune with real data)

**Line of fire (HC-SR04)**
- Own thread at ~20–40 Hz, median filter over last ~5 readings.
- Closing speed = distance drop ÷ time. Trigger if distance < ~1.5 m AND closing speed > ~2 m/s, with a cooldown.
- Limits: ~15° cone, ~4 m range, ~60 ms between pings. Soft foam reflects ultrasound poorly, so test the demo object early (a firmer ball or a box may work better). Backup options: ToF sensor (short range), camera-based detection, slower demo object.

**Fall / impact (MPU-6050)**
- Fall: near free-fall (~0 g for ~100+ ms) → impact spike (several g) → stillness.
- Impact / hat knocked off: big spike without the free-fall phase.
- Ignore IMU for ~200–300 ms after each buzz so the motor doesn't cause false impacts.

**Haptics (DRV2605L)**
- Call `use_ERM()` for disc motors; pick a strong, distinct "danger" waveform.

---

## 5. System architecture

```
HAT (Raspberry Pi)
  Webcam → ffmpeg rolling buffer (5 s segments in a ring, keep last 12–18)
  HC-SR04 thread → approach-speed calc ─┐
  MPU-6050 thread → fall/impact detect ─┴→ TRIGGER → DRV2605L buzz (line of fire)
                                              │
            event JSON + keyframes + clip → local queue (SD) → upload when network is up
                         │  Wi-Fi (own phone hotspot / travel router)
                         ▼
LAPTOP BACKEND: FastAPI + SQLite + clips folder + WebSocket + asyncio job queue
  → vision LLM on keyframes + sensor JSON → validated JSON report + risk score
                         ▼
DASHBOARD: live event feed, clip playback, IMU/distance trace, per-incident report,
           risk matrix, weekly report + safety-meeting mission, PDF export
```

### Proposed stack (not locked)
- **Hat:** Raspberry Pi OS 64-bit, Python 3, `gpiozero` (RPi.GPIO does NOT work on Pi 5), `smbus2`, `adafruit-circuitpython-drv2605`, an MPU-6050 lib, ffmpeg.
- **Hat → backend:** HTTP POST with a local retry queue (store-and-forward).
- **Backend:** FastAPI + SQLite.
- **LLM:** vision model given ~8–20 keyframes + sensor JSON, returning a fixed JSON schema. Provider undecided; default candidate is a fast Gemini Flash-tier model, to be benchmarked against one alternative. **Groq has not been chosen or considered** (the doc titled "Groq Summary from Shreay" is just Shreay's notes, not a provider decision). Who has the API key/credits is still open.
- **Frontend:** React/Next.js (or Streamlit if short on time) + WebSocket for live events.
- **Network:** our own hotspot/router; venue Wi-Fi often blocks device-to-device traffic.

### Clip length (undecided)
- Dhir's context doc: last ~60–90 s pre-event + ~10 s after.
- Shreay's notes: 10 s before and 10 s after.

---

## 6. Backend plan and hat ↔ backend contract

### Event: `POST /events` (multipart: `event` JSON + `frames[]` JPEGs)
```json
{
  "event_id": "uuid generated on the hat",
  "hat_id": "hat-01",
  "worker_id": "W-003 (anonymized)",
  "type": "line_of_fire | fall | impact | hat_removed",
  "timestamp": "2026-11-07T14:32:05-07:00",
  "zone": "B",
  "sensor": {
    "distance_m": 0.9,
    "closing_speed_mps": 3.1,
    "peak_accel_g": 4.2,
    "freefall_ms": 180,
    "imu_trace": [[t_ms, ax, ay, az], "..."]
  },
  "frame_times_ms": [-5000, -3000, -1500, -500, 0, 500, 1500, 3000]
}
```
- **Clip:** `PUT /events/{event_id}/clip` (MP4/H.264), sent after the event, retried until success. The report runs from keyframes, so the clip isn't on the critical path.
- **Idempotent:** a repeated `event_id` returns 200 and does nothing (safe retries).

### Report (LLM output, validated with Pydantic)
```json
{
  "event_type": "near_miss | struck_by_risk | fall | impact | hat_removed",
  "summary": "1–2 sentences",
  "description": "only what is visible or measured",
  "contributing_factors": ["housekeeping", "line of fire", "..."],
  "likelihood": 1-5,
  "severity": 1-5,
  "corrective_actions": [{"level": "eliminate|substitute|engineering|admin|ppe", "action": "..."}],
  "watch_for": ["..."],
  "confidence": "low|medium|high"
}
```
- Backend computes `score = likelihood × severity` (in code, not by the LLM). Colours: 1–4 green, 5–12 yellow, 15–25 red. Severity floors: fall ≥ 4, impact ≥ 3, line_of_fire ≥ 3.
- Invalid output → retry once → sensor-based template fallback (`is_fallback = true`). The dashboard never shows a broken report.

### Other endpoints / pieces
- `GET /events` (newest first, filter by type/zone), `GET /events/{id}` (event + report + clip URL), `/clips/...` static files.
- WebSocket `/ws` messages: `event_created`, `clip_received`, `report_ready`, `report_failed` (each with `event_id`).
- DB tables: `events`, `reports` (incl. model, latency_ms, is_fallback), `shift_reports` (aggregates + mission).
- `POST /shift/end`: counts by type/zone, trend vs last period, top hazards, highest-score incidents → text model writes the mission (toolbox-talk topic, 3–5 talking points, one measurable weekly goal).
- Demo safety: `POST /admin/reset`, `POST /admin/replay/{event_id}`, backup-report toggle, seed week of sample data flagged `is_sample` and labelled "Sample data".
- Fake-hat simulator script so frontend/backend can be built without hardware.
- API key in `.env` + `.gitignore`, never in the public repo.

### Phases
- **Phase 0 (before Fri 7 PM, no code):** owners, API access, laptop setup (Python 3.11+, ffmpeg, hotspot IP), agree on contract, collect report-form references.
- **Phase 1 (Fri night):** repo skeleton, DB, event/clip endpoints, read endpoints, WebSocket, simulator. Checkpoint: simulated event shows live and clip plays.
- **Phase 2 (Sat):** provider-agnostic `generate_report()`, prompt, validation, scoring, job queue, model benchmark (target < ~20 s, valid JSON), real-hat integration + store-and-forward test. Checkpoint: real event → buzz → dashboard → report in < 1 min.
- **Phase 3 (Sun):** shift aggregation + mission, seed data, demo switches, PDF export, timed rehearsals (×3+).
- **Phase 4 (Mon AM):** code freeze ~2 h before deadline, README, key check, submit by 11 AM.

---

## 7. Safety report format (make it look like real site forms)

Per incident: date/time, site, zone (AprilTag/QR marker), anonymized worker ID, event type, description, contributing factors (housekeeping, PPE, equipment, line of fire…), risk matrix (Likelihood 1–5 × Severity 1–5, colour-coded), corrective actions by hierarchy of controls (eliminate → substitute → engineering → admin → PPE), evidence (clip + distance/IMU trace).

Weekly: counts by type/zone, trend vs last week, top 3 hazards, zone heat map, one "mission" for the next safety meeting.

Research task: copy fields and look from real near-miss/incident forms and toolbox talks (Alberta OHS, COR program, typical GC forms).

---

## 8. Connectivity story

- **Primary:** Wi-Fi upload of events and clips.
- **No signal:** store locally, upload on reconnect (store-and-forward). Strongest, honest answer.
- **Roadmap / optional demo:** urgent alerts over low-bandwidth radio (tiny packets like "FALL, worker 3, zone B", never video). NAT kit has a 315 MHz RF TX/RX for a demo; LoRa is the real-world version.
- **Don't pitch CAN or I2C as a radio fallback:** CAN is wired; I2C is board-level only.

---

## 9. Problem, market and competition

**Problem:** sites learn from injuries, not from the close calls before them. Near misses mostly go unreported because reporting is manual, awkward and can feel like snitching, so safety meetings are generic.
- Safety pyramid (~300 near misses per major injury, IHSA): researchers treat the exact ratio as more myth than law, so use it as an idea, not a hard stat.
- Hook stats (re-verify before use): Canada 183 construction deaths in 2022 (AWCBC); US 1,075 in 2023 (BLS).

**Market (global, 2025, USD — from Shreay's research):**
- Smart PPE overall ≈ $4.9B, growing to ≈ $10.2B by 2031 (~13%/yr) (ResearchAndMarkets).
- Construction wearables ≈ $4.6B (EHS Today).
- Smart PPE for construction ≈ $623M, growing ~15%/yr to ≈ $1.9B by 2033; North America is the largest region (Grand View Research).
- Beachhead: Canada, ~1.65M construction workers (StatCan), plus Alberta oil & gas.
- Bottom-up example: 1.65M workers × $100/worker/yr ≈ $165M. **The $100 is a made-up assumption; label it so.**
- Still need: proper TAM/SAM/SOM with sources, and pricing.

**Business model:** per-hat monthly fee + per-site dashboard tier; free pilot → paid. Buyers: safety managers at general contractors and subcontractors.

**Competition:** SenseHuman AI (Edmonton): 4-camera 360° smart hard hat with real-time directional vibration, pilot with The Bartlett Group. They already warn in real time, so differentiate on the report/learning loop, cost, simplicity and retrofit. Verify claims about what they lack.

---

## 10. Demo and pitch

**Pitch flow (15 min):** hook stats → problem → introduce Argus as "just" a smart hard hat → live demo → "one more thing" dashboard reveal → market → business model → competition → roadmap → honest limits.

**Live demo:**
1. A teammate (or judge) wears the hat in a mock site with tape zones.
2. Someone tosses an object at the hat from outside their view → it buzzes, logs a struck-by near miss.
3. Fall / hat-knocked-off / drop test → fall alert, clip saved.
4. Hit "end of shift" → dashboard already shows the demo incidents with clips, reports, risk matrix, plus the weekly report and safety-meeting mission.
- Report generation runs in the background during the pitch (target < 1 min). Keep a pre-generated backup report and a recorded backup demo video.
- Clear the throwing demo with organizers; use a soft-but-detectable object.

**Roadmap:** radio alerts, rear/360 camera, better CV, fatigue detection, EEG (long term).

**Honest limits:** false positives (cooldowns + human review), privacy (blur faces, log events not individuals, on-device where possible), front camera helps less with falls, demo-grade battery life.

---

## 11. Open questions / to-do before and during the event

- Pi 4 vs Pi 5; one vs two cameras; clip length (60–90 s vs 10+10 s).
- Captain and role owners.
- Which LLM provider; who has the API key/credits; measured upload + LLM latency.
- Can the HC-SR04 catch a thrown object? If not, plan B.
- Mounting positions; enclosure CAD.
- Ask NAT: soldering on borrowed boards, OK to throw an object in the demo.
- Breadboard tests (throwaway, not repo code): HC-SR04 with divider, MPU-6050, DRV2605L + motor; power bank under load; webcam power draw at 720p 15–30 fps.
- Bring list: power bank, cables, soldering iron, hotspot/router, Velcro/zip ties, AprilTag zone markers, demo object, spare microSD.
- Pricing numbers and sourced TAM/SAM/SOM.
