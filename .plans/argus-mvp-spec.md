# Argus — MVP Spec

> Status: draft from interview, 2026-11-06. Updated with the modular hardware-integration plan (§5). **Spec only, no code yet.**
> Goal: startup MVP plus a live pitch demo. Polished frontend and backend, a working hardware prototype.

## 1. Product

Argus is a retrofit kit for a standard hard hat. It works like a dash cam for construction workers:

- Keeps a **rolling video buffer** on the hat.
- Detects **line-of-fire / struck-by**, **falls / head impacts**, and **hat knocked off / removed**.
- Gives the wearer an **immediate haptic warning** (directional: top or back of head).
- On every incident or near miss, saves a **clip (20s before / 10s after)** and pushes the **sensor trace** to the backend.
- An LLM pipeline turns each event into a **Canadian-style incident / near-miss investigation report**, which a **site safety manager** reviews in a dashboard.

### Hazards

| Hazard | Response | Sensors |
|---|---|---|
| Line of fire / struck-by (incl. overhead falling objects) | Real-time directional haptic + logged event + clip + report | 5× HC-SR04 (4 rear covering ±30°, 1 top) + IMU ego-motion compensation |
| Fall / head impact | Haptic (both motors, long) + logged event + clip + report + dashboard alert | MPU-6050 IMU |
| Hat knocked off | Logged event (higher severity) + clip | ToF (hat-off) preceded by an IMU spike |
| Hat removed | Logged PPE-compliance event | ToF (hat-off) with no IMU spike |
| Environment (context only) | Attached to every event report, no alerts | SCD41 (CO₂ / temp / RH) |

### Out of scope for MVP
- Privacy features (face blurring, retention policy). Skipped for the hackathon. Pitch line: "clips are saved only on events, never streamed".
- Store-and-forward and offline sync. The demo uses venue Wi-Fi.
- Full-shift battery life. A 2–3h demo runtime is accepted.
- Live alert feed, trend charts and crew-status screens. **Only the Incident Detail + Report screen is required**, plus a minimal incident list to navigate to it.
- Regulatory compliance filing. Reports are an **OHS-style structure, not a compliance document**.
- **GPS / positioning.** There is no GPS module. The site comes from the device's assignment (worker → crew → site). The exact spot on site is never inferred, from video or anything else: reports always show "Location to be determined", and the manager can fill it in at sign-off.

---

## 2. Hardware

### Bill of materials (in use)

| Part | Role | Notes |
|---|---|---|
| Raspberry Pi 4 (1GB) | Main controller | All sensing, buffering and upload |
| Pi Cam V2.1 (CSI) | **Primary camera**, front-facing | IMX219, hardware H.264. **62.2° H × 48.8° V FOV** (narrow for a dash cam; see §2.6) |
| USB webcam ×1 (of 2 available) | **Rear camera (stretch, phase 2)** | MJPEG, low res |
| HC-SR04 ×5 (of 7) | Struck-by ranging | 4 rear (−22.5°, −7.5°, +7.5°, +22.5°) + top. 15° beam each, see §3.2 |
| MPU-6050 (GY-521-style breakout) | Fall/impact, ego-motion, hat-off spike | 6-axis, ±16g / ±2000°/s, I2C 0x68. See §2.6 |
| Adafruit VL53L0X ToF (#3316) | Hat-on detection | Inside crown, facing down. Adafruit guide: **~50–1200mm** usable range |
| SCD41 | CO₂ / temp / RH context | Periodic mode, 5s. **175mA peak** current draw. Readings are biased by the wearer's breath and head heat (§2.6) |
| L293DNE | Motor driver | Dual H-bridge, 600mA/ch, internal clamp diodes |
| 2× Adafruit 1201 vibration discs | Haptics | Top-of-head + back-of-head |
| Anker Nano 5,000mAh (A1653) | Power | 5V⎓3A max at 5V (the 22.5W rating is only at 9–10V PD, which the Pi doesn't use). About 18.5Wh. Demo only |
| Arduino UNO R3 | **Fallback only** (see §2.4) | |
| ~~Adafruit 9-DOF~~ | Replaced by the MPU-6050. Not used. | |

### 2.1 Motor drive (Pi-only, primary)

- L293DNE **Vcc1 (pin 16) = 5V** logic, **Vcc2 (pin 8) = 5V** motor supply from the 5V rail. Common ground with the Pi.
- Pi 3.3V logic is a valid HIGH for the L293D (VIH min ≈ 2.3V), so no level shifter is needed.
- Each motor runs on one half-bridge (motor between output and GND). Tie IN high and **PWM the EN pin** for intensity, or tie EN high and PWM IN. Each motor needs 1 PWM pin (2 if using EN+IN).
- **Datasheet (TI L293D):** VOH = VCC2 − 1.4V typ / − 1.8V worst case at 600mA. The drop is somewhat lower at our ~80mA. With the motor wired high-side to GND, only that one drop applies, so each motor sees about **3.2–3.6V**. That is inside the 1201's 2–5V rating, where it draws about 60–80mA and gives medium-strong vibration (Adafruit: 60mA @ 3V, 100mA @ 5V).
- Add a 100µF + 0.1µF cap across Vcc2 near the chip. Motor inrush otherwise browns out the Pi's 5V rail.

### 2.2 HC-SR04 wiring
- VCC = 5V. **The ECHO line outputs 5V and needs a divider to the Pi** (e.g. 1kΩ / 2kΩ → about 3.3V). This is non-negotiable. A direct connection can damage the GPIO.
- Each sensor gets its own TRIG and ECHO pin. **Do not share TRIG**, because simultaneous pings cause crosstalk.

### 2.3 GPIO budget (Pi 4, BCM)

| Function | Pins |
|---|---|
| I2C bus (IMU, VL53L0X, SCD41) | GPIO2 (SDA), GPIO3 (SCL) |
| Motor top (EN1, PWM) | GPIO12 (hardware PWM0) |
| Motor back (EN2, PWM) | GPIO13 (hardware PWM1) |
| HC-SR04 R1 (−22.5°) TRIG / ECHO | GPIO5 / GPIO6 |
| HC-SR04 R2 (−7.5°) TRIG / ECHO | GPIO19 / GPIO26 |
| HC-SR04 R3 (+7.5°) TRIG / ECHO | GPIO23 / GPIO24 |
| HC-SR04 R4 (+22.5°) TRIG / ECHO | GPIO16 / GPIO25 |
| HC-SR04 Top TRIG / ECHO | GPIO20 / GPIO21 |
| Spare for 6th/7th HC-SR04 (layout E) | GPIO22 / GPIO27, GPIO7 / GPIO8 (SPI must stay disabled) |
| Status LED (optional) | GPIO17 |

I2C address check: VL53L0X 0x29, SCD41 0x62, MPU-6050 0x68 (tie AD0 to GND so it can't float to 0x69). No conflicts. Run `i2cdetect -y 1` on day one.

MPU-6050 INT → **GPIO4** (optional hardware motion/impact flag).

### 2.4 Arduino fallback (only if Pi-driven haptics fail)
- **Fallback A (haptics only):** the UNO drives the L293D. The Pi sends ASCII commands over USB serial at 115200 baud: `H,<top|back|both>,<pattern_id>\n`. The UNO plays the pattern and acks with `OK\n`. Haptic latency budget is under 30ms end to end.
- **Fallback B (haptics + ultrasonics):** the UNO also does the HC-SR04 timing (no divider needed, 5V native) and streams `U,<id>,<mm>,<t_us>\n`. The Pi still does all fusion logic. Use this if Python GPIO echo timing is too jittery (more than ±1cm).
- Both fallbacks are just alternative implementations of the `HapticDriver` and `RangeArray` contracts (§5.2), selected in `config.yaml` (§5.3).

### 2.5 Physical layout
- **Pi Cam:** front brim, slight downward tilt (about 10°) so it sees hands and work.
- **Rear USB webcam (phase 2):** back of the shell, facing rearward.
- **HC-SR04 R1–R4:** a fan across the back of the shell, aimed at −22.5°, −7.5°, +7.5° and +22.5° from straight back (in the horizontal plane, tilted about 5° up so the beams clear the shoulders). With 15° beams this gives contiguous ±30° rear coverage. Use a 3D-printed bracket to hold the angles: a ±5° aiming error opens gaps.
- **HC-SR04 top:** crown, facing straight up (overhead falling objects and swinging loads).
- **VL53L0X:** inside the crown, facing down toward the head. **Mount it so the on-head distance is at least 50–80mm.** Adafruit's guide gives about 50mm as the near limit, although the product title says "~30mm". Design to the conservative figure. Crown clearance in a hard hat is only about 25–35mm, so **recess the sensor into a printed pocket in the shell** or aim it at an angle across the crown to lengthen the path. It must not be obstructed by the suspension straps.
- **Motors:** the top motor against the crown pad, the back motor against the nape strap. Both need skin or pad contact.
- **Pi + battery:** rear brim or a back-mounted enclosure, used as counterweight to the front camera.

### 2.6 Datasheet check (all sensors)

| Part | Spec that matters | Source value | Impact on design |
|---|---|---|---|
| **HC-SR04** | Beam angle, range, current | <15° total, 2cm–4m, 15mA, ≥60ms between pings | Drove the 5-sensor layout (§3.2). 5 × 15mA = 75mA on 5V |
| **Adafruit 1201 motor** | V / I | 2–5V; 40mA @ 2V, 60mA @ 3V, 80mA @ 4V, 100mA @ 5V; 11,000 RPM @ 5V | L293D drive is correct. Arduino fallback **without** a driver needs a 100–1000Ω series resistor per Adafruit, which gives weak vibration |
| **L293D(NE)** | Logic threshold, output drop, diodes | VIH min 2.3V; VOH = VCC2 − 1.4V typ; internal clamp diodes; VCC2 ≥ VCC1 (4.5V min) | Pi 3.3V logic is fine. Motors get about 3.2–3.6V |
| **VL53L0X** | Range | ~50–1200mm (Adafruit guide); ~30mm (product title) | **On-head distance must be ≥ 50mm**, which needs a recessed or angled mount (§2.5) |
| **SCD41** | Supply, current, interval, accuracy | 2.4–5.5V; **175mA peak / 15mA avg** (periodic); 5s update; CO₂ ±(40ppm + 5%) | Power it from **5V** if the breakout has a regulator, not the Pi's 3.3V pin. Add a 10µF cap near it. See bias note below |
| **Pi Cam V2.1** | FOV, modes | 62.2° H × 48.8° V; 1080p30, 720p60, 640×480p90 | Narrow FOV, see the camera note below |
| **Anker Nano A1653** | Output | 5V⎓3A (15W) on the 5V profile | Meets the Pi 4's 3A spec, but at 5.0V not the Pi's 5.1V, so expect occasional **under-voltage warnings** under load. The built-in plug (no cable) minimizes drop |
| **Pi 4 GPIO** | Pin current | ~16mA max per pin | Nothing is driven directly. The L293D and the dividers handle that |

**IMU: MPU-6050 (6-axis accel + gyro, no magnetometer).** It replaces the Adafruit 9-DOF, which is no longer used.

| Spec (MPU-6000/6050 product spec) | Value | Impact on Argus |
|---|---|---|
| Accel full-scale | ±2 / 4 / 8 / **16g** | Use **±16g**. The 6g impact threshold works. Saturation still applies (below) |
| Gyro full-scale | ±250 / 500 / 1000 / **2000 °/s** | Use **±1000 °/s**. Head turns rarely exceed about 600°/s, and this keeps resolution for the 60°/s rotation gate |
| Accel output rate | up to 1kHz | Sample at **500Hz–1kHz into the FIFO** for impact peaks |
| FIFO | 1024 bytes | At 1kHz with 12 bytes per sample (accel + gyro), it holds about 85ms. **Drain it every ≤ 40ms** in bursts, or it overflows and samples are lost |
| Digital low-pass filter | configurable | Set **DLPF_CFG = 0 (~260Hz bandwidth)**. Heavier filtering smooths away impact peaks |
| Motion-detect interrupt | MOT_THR / MOT_DUR registers, INT pin | Optional: wire INT to a GPIO as a hardware impact flag. Free-fall-interrupt registers appear only in older register-map revisions, so **detect free-fall in software** from FIFO data |
| I2C address | **0x68** (AD0 low) / 0x69 (AD0 high) | No conflict with VL53L0X (0x29) or SCD41 (0x62) |
| Chip VDD | 2.375–3.46V | **Never put 5V on the bare chip.** On a GY-521-style breakout, VCC goes through an onboard 3.3V regulator. Power it from 3.3V and keep the I2C pull-ups at 3.3V for the Pi |
| On-chip DMP (motion processor) | quaternion output via proprietary firmware | **Not used.** Do orientation fusion on the Pi (Madgwick/Mahony on 6-axis data) |

**No magnetometer: what it costs us.**
- **Yaw (heading) drifts.** Ego-motion compensation doesn't need absolute heading. It needs gravity removal (pitch and roll, which are well-observed from the accel) and hat-frame velocity along each sensor axis over about 1s windows. Yaw drift over 1s is negligible, so **§4.1 still works unchanged.**
- What we lose: absolute compass direction of the threat in reports. Reports use hat-relative directions ("from behind-left"), which is clearer for a safety manager anyway.

**Head impacts saturate any ±16g part.** Real hard-hat impacts can exceed 50g. The `peak_g` field therefore records **"≥ 16g (saturated)"** when clipped, and reports must not quote it as an exact value. For the MVP this is acceptable: we detect that an impact happened and its timing, not its exact force. A high-g accelerometer such as the ADXL375 (±200g) is the production path.

**SCD41 readings on a hard hat are biased:**
- **CO₂:** the wearer's exhaled breath near the face reads high.
- **Temperature:** head heat and the Pi 4 (which runs at 50–70°C) read high.

Mitigations:
- Mount it on the **outer top-rear of the shell**, away from the Pi and the face, with airflow.
- Use the SCD41's temperature-offset setting.
- Label values in reports as **"near-wearer conditions (indicative)"**.

This is acceptable because the SCD41 is context only.

**Camera FOV:**
- **Sensor mode:** the IMX219's 1920×1080 mode is a **center crop** (narrower FOV still). For full width, use the binned **1640×1232** sensor mode and output 1280×720 from it (set `sensor={'output_size': (1640,1232)}` in picamera2).
- **Coverage:** 62° horizontal shows the work in front of the worker but not much periphery.
- **Production path:** if a wider view is wanted, Camera Module 3 Wide (102° H) is a drop-in CSI replacement.

**5V power budget (peak):**

| Load | Current |
|---|---|
| Pi 4 (load, encoding) | ~1.0–1.3A |
| USB webcam (phase 2) | ~0.25A |
| 2 motors | ~0.16A |
| 5 HC-SR04 | ~0.08A |
| L293D logic | ~0.06A |
| SCD41 peak | ~0.18A |
| **Total** | **~1.7–2.0A** |

That is within the 3A supply. Runtime is about 18.5Wh × ~85% ÷ ~5–6W ≈ **2.5–3h**, which is enough for the demo.

---

## 3. Decision matrices

Scores are 1–5 and weights add up to 100. Weighted score = Σ(weight × score) / 100.

### 3.1 Camera setup

Concerns raised: webcam latency, decode load, and whether the Pi 4 (1GB) can handle multiple streams.

Key facts:
- The Pi 4 has **one hardware H.264 encoder** (up to about 1080p30). The Pi Cam via `picamera2` encodes in hardware, with almost no CPU and a small RAM ring buffer (720p30 at about 3 Mbps ≈ 11MB per 30s).
- USB webcams usually deliver **MJPEG**. **We never need to decode it on the Pi**: store raw JPEG frames in a ring buffer and package them as `.avi` (`ffmpeg -c copy`) on event. Transcode to MP4 on the backend. 640×480 @ 15fps MJPEG is about 15–20MB per 30s of RAM.
- The struck-by sensors face **rear and top**, so a front-only camera **will not see the threat** in a struck-by-from-behind clip. That gap is the main reason to add a rear camera.
- LLM cost of a second clip is about +$0.03 per event (see §3.3), which is negligible.

| Criterion (weight) | A: Pi Cam front only | B: 1 USB webcam only | C: Pi Cam front + USB rear (low-res) | D: Pi Cam + 2 USB (3 cams) |
|---|---|---|---|---|
| CPU / encode load (20) | 5 | 3 | 4 | 1 |
| Latency & sensor sync (15) | 5 | 3 | 4 | 2 |
| RAM headroom on 1GB (15) | 5 | 3 | 4 | 2 |
| Hazard coverage (25) | 2 | 2 | 5 | 5 |
| LLM evidence value (10) | 3 | 3 | 5 | 4 |
| Build/demo risk (15) | 5 | 4 | 3 | 1 |
| **Weighted** | **4.05** | **2.90** | **4.20** | **2.60** |

**Decision: C, built in two phases.**
1. **Phase 1 (must work):** Pi Cam front, 1280×720 @ 30fps, H.264, `picamera2` `CircularOutput`, **from the full-FOV 1640×1232 binned sensor mode** (§2.6). This alone is a complete demo.
2. **Phase 2 (stretch):** rear USB webcam at 640×480 @ 15fps, MJPEG ring buffer, no decode on the Pi. If CPU goes over about 70% or frames drop, disable it with a config flag.
3. **Three cameras: no.** A side or second-rear view adds little over C, and it costs RAM, USB power draw and battery.
4. Every frame from every camera is timestamped with `CLOCK_MONOTONIC`, on the same clock as the sensors.

### 3.2 Ultrasonic layout

> **Revised.** v1 of this section assumed about 30° beams and recommended 2 rear-quarter sensors at ±35°. The datasheet gives **15° total** ("effectual angle <15°"), so that layout left a **55° blind wedge directly behind the head**. v1 also miscounted the timing limit: it is about 8 sensors, not 4. Corrected below.

**Datasheet facts (HC-SR04):**
- Range: 2cm–4m (rated).
- Measuring / effectual angle **<15° total** (about ±7.5° off-axis).
- 40kHz, 10µs trigger pulse.
- Datasheet recommends **≥60ms between measurements** for a single sensor, to avoid the previous ping's echo.

**Beam footprint (15° full cone):**

| Distance | Beam width |
|---|---|
| 0.5 m | 0.13 m |
| 1.0 m | 0.26 m |
| 2.0 m | 0.53 m |

A single sensor is a narrow "pencil". Coverage has to come from **sensor count and aiming**, not from cone overlap.

**Object size widens effective coverage.** An object of width W at distance d is caught if any part of it enters the beam, which adds about ±atan(W / 2d):
- **2×4 board / 30cm load at 1.5m:** about ±5.7°, so the effective cone is about 26°.
- **5cm pipe at 1.5m:** about ±1°, so it stays at about 15–17°.

Design for the **thin-object case**, the worst case.

**Effective range is shorter than 4m for real targets.** Small, round, soft (clothing, foam) or angled surfaces scatter the echo. Expect reliable detection of a thin pipe or angled board only to about 1–2m. The 2.0m trigger zone sits at that edge, so it must be validated with the beam test below.

**Timing:**
- Sequential firing at a **30ms slot per sensor** gives sweep = 30ms × N. The 60ms datasheet gap applies per sensor, and each sensor still waits ≥ 30ms × N between its own pings, which satisfies 60ms when N ≥ 2.
- Closing at 1.5 m/s from 2m leaves about 1.3s.
- Least-squares closing rate needs ≥ 5 samples per sensor, so sweep ≤ 260ms, so **N ≤ 8**.
- **Fire order is interleaved** so that consecutive pings come from non-adjacent sensors (e.g. R1 → R3 → Top → R2 → R4). That limits adjacent-beam crosstalk.

**Coverage requirement (gate):** contiguous coverage of the **priority zone, straight back ±30°** (60°), plus a **top sensor**. With 15° beams, the number of rear sensors needed = ⌈60° / beam⌉ = **4**. Any layout with a blind spot straight behind **fails the gate**, whatever its score.

| Criterion (weight) | A: 2 rear-quarter (±35°) + top *(v1)* | B: 2 rear centered + top (3) | C: 3 rear + top (4) | **D: 4 rear + top (5)** | E: 6 rear (±45°) + top (7) |
|---|---|---|---|---|---|
| Gate: no blind spot straight behind | ❌ **fails** (55° gap) | ✅ | ✅ | ✅ | ✅ |
| Priority-zone (±30°) coverage | 0% of center | 50% | 75% | **100%** | 100% + out to ±45° |
| Sweep period / samples in 1.3s | 90ms / 14 | 90ms / 14 | 120ms / 11 | **150ms / 8** | 210ms / 6 |
| GPIO pins | 6 | 6 | 8 | **10** | 14 |
| Line-of-fire coverage (40) | — | 2 | 3 | **5** | 5 |
| Per-sensor update rate (20) | — | 5 | 5 | **4** | 3 |
| GPIO cost (10) | — | 5 | 4 | **3** | 2 |
| Crosstalk / false-alarm risk (15) | — | 5 | 4 | **4** | 3 |
| Build complexity (15) | — | 5 | 4 | **3** | 2 |
| **Weighted** | **excluded** | **3.80** | **3.80** | **4.15** | **3.55** |

Coverage weight went up from 30 to 40, because coverage is the core function and v1's errors were in exactly that area.

**Decision: D, with 4 rear sensors + 1 top (5 total).**
- Rear sensors are aimed at **−22.5°, −7.5°, +7.5°, +22.5°** from straight back, giving contiguous ±30° coverage.
- The top sensor faces straight up.
- 150ms sweep, about 6.7 Hz per sensor, about 8 samples in the reaction window.
- 10 GPIO pins (see §2.3). GPIO is tight but fits: 18 of 26 usable pins used in total.
- **E (6 rear + top)** is the upgrade path if field testing shows threats coming from wider than ±30°. It still meets timing (210ms sweep, 6 samples).

**Beam test, run on day 1 before mounting anything (about 30 min):**
1. Put the sensor on a table. Place targets on a protractor arc at 0.5m, 1.0m, 1.5m and 2.0m. Targets: (a) 2×4 board edge-on, (b) 5cm PVC pipe, (c) a person in a hoodie.
2. Step each target sideways, and record the angle where detection drops below 80% of 20 pings.
3. **Decision rule:** rear sensor count = ⌈60° / measured angle for the thin pipe⌉.
   - Measured about 15° → 4 rear (layout D).
   - Measured about 20° → 3 rear at −20°, 0°, +20°.
   - Measured ≥ 30° → 2 rear at ±15°.
4. Also record the **max reliable range** per target. If the pipe or hoodie fails beyond about 1.5m, cut the trigger range to 1.5m and lower the TTC threshold to about 1.0s (see §4.1).
5. Run a crosstalk check: fire all 5 sensors in the interleaved order with no targets in front, and confirm there are no phantom readings under 2.5m.

### 3.2a Sensor count review (2026-11-07): PROPOSED, awaiting team sign-off

**Recommendation: layout B, 4 rear sensors and no top sensor, with `min_track_samples: 3`.** The current config stays at 5 sensors until the team agrees. Full matrix and evidence: the "Argus Sensor Layout" page. Reproduce the numbers with `scripts/sensor_layout_study.py`.

- **Top sensor:** in simulated drops from 1–10 m, the 5-sensor layout gave no warning. A 4-sensor layout with the top sensor gave at most 0.18 s. The IMU still logs overhead hits, so clips and reports are kept.
- **The 47% back / 23% top statistic is unverified.** No source found. B still wins with those figures used as weights.
- **Totals out of 500:** B 370, D 360 (fails the gate at a 15° beam), F and G 340 (fail the gate), E 325, A 290, C 240.
- **Delay:** the limit is the sweep (30 ms × sensors) and readings per sensor, not processing. 5 sensors with 4 readings takes 750 ms; 4 sensors with 3 readings takes 480 ms.
- **Beam test sets the final count:** about 15° wide gives 4 rear, at least 20° gives 3 (D), at least 30° gives 2 (G). Run it with boards vertical, as mounted.
- **GPIO:** 5 sensors use 16 of 26 pins, not the 18 stated in §3.2. 4 sensors use 14.
- **Dividers:** one per echo line, all on one soldered hub board at the Pi. Do not share echo or trigger lines. Solder wires at the sensor, use polarized JST-XH at the hub, and no jumpers on the Pi header.
- **Power:** sensor count changes the total by about 1%. The risk to test is motor noise on the 5 V rail.
- **CAD:** one printed curved pod with boards vertical (15° spacing is about 30 mm at a 115 mm shell radius; measure the hat). Nothing on the crown. Do not drill the shell.

If adopted: remove `TOP` from the range `order` and `pins` options, set `min_track_samples: 3`, and keep the top motor for the fall/impact buzz only. The detector already works without TOP, as the study ran it that way.

### 3.3 LLM pipeline

Requirements: **full video clip + sensor data** as input, a report ready **within 5 minutes** of the event, a **$60 total budget** for development and demo, and **no keys yet**.

Key facts (⚠️ re-check prices on the provider pricing pages when signing up. Gemini figures came from third-party sources):
- **Gemini** accepts **video files natively** (video + audio track, timestamp-aware). Older Google docs say video costs about **258 tokens/s at 1 fps** plus about 32 tokens/s of audio, so a 30s clip ≈ 9k tokens. Flash-tier pricing reported at about **$1.50 / 1M input, $9 / 1M output** (verify the current model name and rate).
- **Claude** takes **images, not video**, so we would send sampled keyframes. Current pricing: **Opus 5.5 $4 / $20**, **Sonnet 5.5 $2 / $10**, **Haiku 5.5 $0.10 / $0.50** per 1M tokens (in/out). Claude is strong at structured long-form reasoning and writing, and supports schema-constrained JSON output (`output_config.format`).
- **OpenAI GPT-5.x**: images via frame extraction. Reported at about $2.50–$5 / 1M input and $15–$30 / 1M output for current flagship tiers (third-party sources, verify).

| Criterion (weight) | A: Gemini Flash single call | B: Gemini Pro single call | C: Claude Sonnet 5.5 (keyframes) | D: Claude Opus 5.5 (keyframes) | E: GPT-5.x (keyframes) | **F: Hybrid — Gemini Flash (video→observations) + Claude Opus 5.5 (report)** |
|---|---|---|---|---|---|---|
| Native video understanding (25) | 5 | 5 | 2 | 2 | 2 | 5 |
| Safety reasoning & report quality (20) | 3 | 4 | 4 | 5 | 4 | 5 |
| Cost per event (15) | 5 | 4 | 4 | 3 | 3 | 4 |
| Latency, under 5 min easily (10) | 5 | 4 | 4 | 3 | 4 | 3 |
| Structured-output reliability (15) | 4 | 4 | 5 | 5 | 4 | 5 |
| Integration simplicity (15) | 5 | 5 | 3 | 3 | 3 | 3 |
| **Weighted** | **4.45** | **4.40** | **3.50** | **3.45** | **3.20** | **4.35** |

**Decision: build A first, then upgrade to F (hybrid).** A scores highest overall, because it is the simplest and cheapest. F scores slightly lower (4.35) because it has two calls and more latency, but it wins on **report quality**, which is what the safety manager and the pitch audience judge. Since F's stage 1 *is* A, choosing F costs nothing: A is the working fallback at every point.

- **Stage 1 — Gemini Flash (vision):** input is the front clip (plus the rear clip in phase 2) and a compact sensor summary. Output is a strict JSON **observation timeline**: timestamped visual facts (what objects are visible, who is nearby, what the worker is doing, PPE visible, environment conditions), with no conclusions. Built first. If stage 2 fails, a Gemini-written report can be generated from the same call with a second prompt.
- **Stage 2 — Claude Opus 5.5 (reasoning/report):** input is the observation timeline, the full sensor trace summary (peak g, closing rate, time to contact, which sensor, hat-on state, SCD41 values), and the worker/site context. Output is the **report JSON schema** (§6.3) via structured outputs. Effort `high`, adaptive thinking.
- **Why hybrid:** Gemini is the only option that actually watches the video. Claude is the stronger writer/reasoner for root cause and hierarchy-of-controls analysis, which is what a safety manager judges. Splitting the stages also keeps visual facts separate from inference, which reduces hallucinated causes.
- **Cost estimate per event:** Gemini about 9–18k input tokens + 2k output ≈ $0.03–0.05. Claude about 8k input + about 4k output (incl. thinking) ≈ $0.11. **Total ≈ $0.15–0.20 per event, so $60 covers about 300+ events.** Set hard spend limits in both consoles (e.g. $25 each) and use 10s test clips during development.
- **Latency estimate:** 10s post-roll + about 3s mux + about 10–30s upload (≈11MB on venue Wi-Fi) + Gemini about 20–60s (includes file processing) + Claude about 30–60s ≈ **1.5–3 min**, inside the 5-minute window.

### 3.4 Event classification: near miss vs incident ✅ decided: D

Scores 1–5 (5 = best); totals are weighted, max 500.

| Criterion (weight) | A: Device rules only | B: LLM decides | C: Device, LLM may upgrade | **D: Device decides, LLM flags, manager confirms** |
|---|---|---|---|---|
| Predictable for the demo (25) | 5 | 2 | 4 | 5 |
| Accuracy, e.g. contact visible only on video (20) | 3 | 4 | 4 | 5 |
| Holds up in an audit or legal review (20) | 4 | 2 | 3 | 5 |
| Resists LLM hallucination (15) | 5 | 1 | 3 | 5 |
| Build effort (10) | 5 | 4 | 4 | 3 |
| Speed (10) | 5 | 3 | 4 | 5 |
| **Weighted total** | 440 | 255 | 365 | **480** |

**Decision: D.**
- **Device class (deterministic, set on the Pi):**
  - `head_impact` ≥ threshold, `fall`, or `hat_knocked_off` → **Incident**;
  - `struck_by_warning` with no impact within ±2s → **Near miss**;
  - `hat_removed` → **PPE log** (no clip, no report).
- **LLM:** may return `suggested_class` + `reclass_reason` (e.g. "video shows the board contacting the hat"). This is advisory only; it never changes the class itself.
- **Manager:** at sign-off, accepts or rejects the suggestion. `final_class` is what appears on the signed report. All three values are stored.
- B was rejected because a classifier that can hallucinate is hard to defend to judges or regulators.

---

## 4. Detection algorithms (Pi)

All sensor samples go into per-sensor ring buffers (about 40s) timestamped with `CLOCK_MONOTONIC`.

| Stream | Rate |
|---|---|
| IMU accel + gyro | 100 Hz |
| HC-SR04 ×5 | about 6.7 Hz each (150ms interleaved sweep) |
| VL53L0X | 10 Hz |
| SCD41 | 0.2 Hz |

### 4.1 Struck-by: closing rate with ego-motion compensation

Problem to solve: when the wearer walks backward toward a wall, the range shrinks, which looks like an approaching object.

For each sensor *i* with a fixed unit direction vector **uᵢ** in the hat frame:

1. **Range track.** Keep the last **4** valid ranges (a 450ms span at the 150ms sweep). Reject a sample if:
   - the range is out of bounds (under 3cm or over 2.5m), or
   - it jumps more than 0.35m from the previous sample. That means a new target, so **reset the track**.
   - **Beam hand-off:** 15° beams are narrow, so a target moving sideways leaves one beam and enters the adjacent one. If adjacent sensor *j* reports a range within 0.2m of sensor *i*'s predicted range within 1 sweep, **continue the same track** on *j* instead of starting a new one.
2. **Measured closing rate** `v_meas = −slope(range vs t)`, by least-squares over the track. A positive value means the range is shrinking.
3. **Ego-velocity estimate** `v_hat`:
   - rotate the accel into the hat frame,
   - subtract gravity (orientation from a 6-axis Madgwick/Mahony filter on the Pi; yaw drift doesn't matter here, see §2.6),
   - integrate with a **leaky integrator** (τ ≈ 1s) to limit drift, and
   - apply **zero-velocity updates** when accel variance is below a threshold for 200ms (standing still).
4. **Ego component along the beam** `v_ego = v_hat · uᵢ`.
5. **Object closing rate** `v_obj = v_meas − v_ego`.
6. **Rotation gate.** If |ω| > 60°/s, the beam is sweeping across the scene. Mark the track invalid and reset it.
7. **Trigger** when **all** of the following hold for **2 consecutive sweeps**. Worst-case detection latency is about 4 samples + 1 confirmation ≈ 0.75s after the target enters the beam. That leaves about 0.5s of warning at 1.5 m/s from 2m, or more for slower approaches. If the beam test shows a shorter effective range, see §3.2 step 4.
   - `range < 2.0 m`
   - `v_obj > 1.5 m/s`
   - time to contact `TTC = range / v_obj < 1.3 s`
   - hat is on
8. **Extra suppression heuristic:** if `v_ego > 0.6 · v_meas` (most of the closing comes from the wearer's own motion), require `v_obj > 2.0 m/s`.
9. **Top sensor:** apply the same logic. Vertical ego-motion (standing up under a ceiling) is the main false-positive source, and the vertical axis is the best-observed one in the IMU, which helps.

All thresholds live in `config.yaml` and are tunable. Log every candidate trigger with all intermediate values, so false positives can be tuned offline from recorded data.

**Haptic (directional):**
- rear sensors R1–R4 → **back motor**;
- top sensor → **top motor**;
- pulse rate scales with TTC (TTC 1.3s → 4Hz, TTC under 0.5s → continuous).
- Target latency from trigger to motor is under 50ms.

**Event:** `struck_by_near_miss`. If the IMU registers an impact (§4.2) within 1.5s after the warning, upgrade the event to `struck_by_contact`.

### 4.2 Fall / head impact

- **Impact:** |a| ≥ **6g**, with the MPU-6050 at ±16g and DLPF ≈ 260Hz (§2.6).
  - Impact pulses last only a few milliseconds, so 100Hz polling can miss the peak entirely. Read the accelerometer at **≥ 400Hz via the IMU's hardware FIFO** in bursts, or use its hardware wake-up/high-g interrupt on a GPIO.
  - The 100Hz stream is still used for ego-motion and orientation.
  - Record `peak_g` as "≥ 16g (saturated)" when clipped.
- **Fall:** free-fall (|a| < 0.35g) lasting ≥ **200ms**, followed by |a| > 3g within 1s. This is computed in software from the FIFO stream, without relying on the chip's free-fall interrupt.
- **Haptic:** both motors, one 1.5s buzz (acknowledges that the event was logged).
- **Events:** `head_impact` (with peak g) and `fall` (with free-fall duration ≈ drop height `h = ½ g t²`).

### 4.3 Hat-on / hat-off

- **Boot sequence (calibration happens at boot only, never re-run during operation):**
  1. Power on with the hat **resting still on a table** → 2s MPU-6050 gyro bias + gravity calibration (LED solid).
  2. LED blinks → put the hat on.
  3. ToF baseline = median ToF over 3s once a stable target is in range.
  - If motion is detected during step 1, restart the 2s window.
- **Hat off:** ToF > baseline + 30mm (or no target) continuously for **3s**. **Hat on:** within baseline ± 15mm for 2s.
- If an IMU spike (>3g) or free-fall happened in the **2s before** hat-off → `hat_knocked_off` (save a clip, severity elevated).
- Otherwise → `hat_removed`: **logged only (decided)**, with an event row and sensor snapshot but no clip, no LLM report and no haptic. Breaks and lunch would otherwise spam reports.
- While the hat is off: suppress struck-by triggers and haptics. Keep buffering.

### 4.4 Event handling
- **Debounce/merge:** a new trigger during an active post-roll window extends the existing event (up to +10s) instead of creating a new clip. Keep a 5s per-type cooldown after that.
- On trigger:
  - stamp `t0`,
  - fire the haptic,
  - keep recording 10s post-roll,
  - write the clip (`t0−20s … t0+10s`) to MP4 with `ffmpeg -c copy` (no re-encode),
  - write the sensor trace JSON for the same window,
  - queue the upload.
- **Upload worker:** a separate process with retry and exponential backoff. Events stay on the SD card until the backend acknowledges them.

---

## 5. Pi software: modular, hot-swappable architecture

> **Working agreement (2026-11-06):** the hardware team is testing each sensor individually *right now*, with their own standalone scripts. Claude writes the full Pi stack (drivers, detection, haptics, camera, upload) **as swappable modules**. Every hardware-facing module has at least two interchangeable implementations: **`argus`** (Claude's) and **`team`** (the hardware team's tested code, wrapped in a thin adapter). If Claude's version misbehaves on real hardware, switching to the team's is a one-line config change. **No code is written until the spec is approved.** Code comes next.

### 5.1 Layers

```
 ┌─────────────────────────────────────────────────────────────────────┐
 │ L4  Uploader → backend (§6)                                         │
 ├─────────────────────────────────────────────────────────────────────┤
 │ L3  Event manager: debounce/merge, clip cut, sensor-trace export,   │
 │     haptic pattern player                                           │
 ├─────────────────────────────────────────────────────────────────────┤
 │ L2  Detectors (pure logic, no hardware): StruckBy, FallImpact,      │
 │     HatState, Orientation/EgoMotion                                 │
 ├─────────────────────────────────────────────────────────────────────┤
 │ L1  Sample bus + ring buffers (timestamped, unit-normalized)        │
 ├─────────────────────────────────────────────────────────────────────┤
 │ L0  Hardware modules (SWAPPABLE): Imu, RangeArray, HatOnSensor,     │
 │     EnvSensor, HapticDriver, Camera                                 │
 │     implementations:  argus | team | mock | replay                  │
 └─────────────────────────────────────────────────────────────────────┘
```

- **Only L0 touches hardware.** L1–L4 never import GPIO, I2C or camera libraries. That makes L1–L4 fully testable on a laptop.
- **L2 detectors are pure functions of samples.** Each one is a class with `update(sample) -> list[Event]` and no I/O. They can be replaced individually too (`detectors.struck_by.impl: argus | team`) if the team prefers their own math.
- **Data flows one way:** L0 → bus → L2 → L3 → L4. Haptic commands go L3 → L0 `HapticDriver`.

### 5.2 Module contracts (the "plug shape")

Every L0 implementation must satisfy one of these interfaces (Python `Protocol`s). Units and timestamps are **fixed by the contract**, so implementations can't silently disagree.

| Module | Interface (methods) | Sample it emits | Units / rules |
|---|---|---|---|
| `Imu` | `start()`, `stop()`, `read() -> list[ImuSample]` (drains everything since the last call), `health() -> Health` | `ImuSample(t_ns, ax, ay, az, gx, gy, gz)` | accel **g**, gyro **deg/s**, hat frame (x fwd, y left, z up). ≥ 400Hz for impact, see §4.2 |
| `RangeArray` | `start()`, `stop()`, `read() -> list[RangeSample]`, `health()` | `RangeSample(t_ns, sensor_id, range_mm \| None)` | **mm**. `None` = no echo / timeout. `sensor_id ∈ {R1,R2,R3,R4,TOP}` |
| `HatOnSensor` | `start()`, `stop()`, `read() -> list[TofSample]`, `health()` | `TofSample(t_ns, range_mm \| None, status)` | **mm**. `status`: `ok \| out_of_range \| error` |
| `EnvSensor` | `start()`, `stop()`, `read() -> list[EnvSample]`, `health()` | `EnvSample(t_ns, co2_ppm, temp_c, rh_pct)` | ~0.2Hz |
| `HapticDriver` | `set(motor, intensity)`, `off_all()`, `health()` | — | `motor ∈ {top, back}`, `intensity` 0.0–1.0. **Patterns are not in the driver.** L3's pattern player calls `set()`, so a team driver only has to turn a motor on at some strength |
| `Camera` | `start()`, `stop()`, `save_clip(t_start_ns, t_end_ns, path) -> ClipInfo`, `health()` | — | Keeps its own ring buffer. Frames are stamped with the shared clock |

**Shared rules for all implementations:**
- **Clock:** `time.monotonic_ns()` only. Never wall-clock time. L4 maps it to UTC once per upload.
- **Non-blocking:** `read()` returns within 5ms. Slow hardware I/O runs in the module's own thread, and `read()` just drains a queue.
- **Health:** `health()` returns `{ok, last_sample_age_ms, error_count, note}`. The supervisor uses it for automatic fallback (§5.4).
- **Errors:** never raise out of `read()`. Log, increment `error_count`, and return what you have.

### 5.3 Implementations per module

| Impl | What it is | Used for |
|---|---|---|
| `argus` | Claude's driver, written to the datasheet details in §2.6 (e.g. the MPU-6050 FIFO driver at ±16g / 1kHz, the interleaved HC-SR04 sweep) | Default once it passes the bench checks |
| `team` | The hardware team's standalone test script, **pasted mostly unchanged** into `hardware/team/<module>/`, plus a thin `adapter.py` that converts its output to the contract (units, timestamps, IDs) | Known-good fallback, already proven on the bench |
| `mock` | Synthetic data from scenario scripts (§5.6) | Developing L1–L4, the backend and the dashboard with no hardware |
| `replay` | Plays back recorded real sensor logs (`.jsonl`) at real or accelerated speed | Tuning detectors, regression tests, demo rehearsal |

Selected per module in `config.yaml`:

```yaml
modules:
  imu:        { impl: argus, fallback: team }
  range:      { impl: team,  fallback: argus }   # e.g. team's HC-SR04 timing proved more stable
  hat_on:     { impl: argus, fallback: team }
  env:        { impl: argus }
  haptics:    { impl: argus, fallback: team }    # team impl may be the Arduino serial version (§2.4)
  camera:     { impl: argus }
detectors:
  struck_by:   { impl: argus }
  fall_impact: { impl: argus }
  hat_state:   { impl: argus }
```

### 5.4 How "hot-swap" works

1. **Planned swap (config):** edit `config.yaml` and run `argus-cli reload <module>`. The supervisor stops that one module, loads the other impl and restarts it, in **under 2s**. The other modules and the ring buffers keep running.
2. **Automatic fallback (runtime):** if a module's `health()` reports not-ok, or no samples for **500ms** (IMU/range) or **3s** (ToF), the supervisor switches to its `fallback` impl. It logs a `module_fallback` event, which is also uploaded so the dashboard shows it. Haptics fallback is critical, so it's checked every 1s with a zero-intensity self-test.
3. **Shadow mode (A/B on real hardware):** `argus-cli shadow imu` runs `argus` and `team` **on the same sensor**, if the bus allows it (or in alternating windows if not), and writes both streams to a diff log. Use it to prove Claude's driver matches the team's before switching the default. Shadowing isn't possible for exclusive-access devices like the camera or haptics. Those use A/B bench runs instead.

### 5.5 Integration path: when the team pastes their code

Paste each tested script into chat with this **hand-off template**:

```
Module: (imu | range | hat_on | env | haptics | camera)
Wiring: pins / I2C address / power used
Libraries + versions: (pip freeze excerpt)
What it outputs: example output lines (10–20 lines of real data)
Units + rate: e.g. "distance in cm, ~10 readings/s"
Known quirks: e.g. "sensor 3 reads 0 sometimes", "needs 2s warmup"
```

What Claude then does, per module:
1. Copy the script into `hardware/team/<module>/` **without rewriting its hardware logic.**
2. Write `adapter.py`: it calls the team code (thread or subprocess), converts units, adds `monotonic_ns` timestamps and maps IDs to the contract.
3. Run the **contract test suite** (§5.6) against the adapter, with the hardware attached.
4. If Claude's `argus` impl exists, cross-check the **wiring and constants** in both. Disagreements (pins, I2C settings, scale factors) are flagged, because the team's bench-proven values win.
5. Set it as `impl` or `fallback` per the team's choice.

If a team script is a blocking `while True: print(...)` loop, the adapter runs it as a **subprocess and parses its stdout**. Zero changes to their code are needed.

### 5.6 Testing without (and with) hardware

| Test layer | What it checks | Runs on |
|---|---|---|
| **Contract tests** (`pytest tests/contract/ --module imu --impl team`) | Units in range (e.g. a stationary IMU reads \|a\| ≈ 1g), timestamps monotonic, rate within ±20% of spec, `read()` returns in under 5ms, `health()` honest, no exception escapes | Pi with hardware attached |
| **Bench self-test CLI** (`argus-cli test <module>`) | Prints live readings plus a pass/fail checklist per module. Examples: "tilt the hat 90°, expect a gravity axis change"; "hand at 50cm in front of R2, expect only R2 to read ≈500mm"; "buzz top, then back" | Pi, done by a human |
| **Scenario mocks** | Synthetic streams for: board swing from behind-left (R1/R2), walking backward into a wall (must **not** trigger), standing up under a beam (TOP, must not trigger), hat drop 1m, hat removed onto a table, hat knocked off | Laptop/CI |
| **Detector tests** | Each scenario produces exactly the expected events (type, sensor, ±100ms timing) | Laptop/CI |
| **Replay regression** | Recorded real sessions (`argus-cli record`) replayed through detectors. Every confirmed false positive or miss becomes a permanent test case | Laptop/CI |
| **Threshold sweep** (`argus-cli sweep`) | All thresholds (closing speed, TTC, impact g, free-fall time, ToF offsets) live in `config.yaml`. Record 20–30 labelled bench trials (walking backward, nodding, standing up, real board swings, drops), then replay them across a threshold grid and pick the setting with the best detection rate at an acceptable false-alarm rate. No hand-tuning by feel. | Laptop |
| **End-to-end with mocks** | mock L0 → real L1–L4 → real backend → dashboard report appears | Laptop + staging backend |

The whole backend, dashboard and LLM pipeline can therefore be built and demoed while the hardware is still on the bench. Hardware joins by switching `impl: mock` → `impl: argus | team`.

### 5.7 Repo layout (Pi side)

```
device/
  argus/
    contracts.py        # Protocols + sample dataclasses + units (§5.2)
    bus.py              # sample bus + ring buffers (L1)
    supervisor.py       # module loading, health checks, fallback, reload (§5.4)
    detectors/          # struck_by.py, fall_impact.py, hat_state.py, orientation.py (L2)
    events/             # event manager, clip cutter, trace export, haptic patterns (L3)
    uploader.py         # L4
  hardware/
    argus/<module>/     # Claude's drivers
    team/<module>/      # team's pasted scripts + adapter.py
    mock/<module>/      # scenario-driven fakes
    replay/             # log player
  scenarios/            # mock scenario definitions (YAML)
  recordings/           # real sensor logs (.jsonl), gitignored except the curated test set
  tests/contract/  tests/detectors/  tests/e2e/
  cli.py                # argus-cli: run, reload, shadow, test, record, replay, trigger, buzz
  config.yaml
```

### 5.8 Runtime details (unchanged from earlier decisions)

- **Language:** Python 3.11, Raspberry Pi OS Bookworm (64-bit Lite). One systemd service runs `supervisor.py`, and modules run as threads.
- **Libraries (argus impls):** `picamera2`, `lgpio`/`gpiozero` (or `pigpio` for better echo timing), `smbus2` with a custom MPU-6050 FIFO driver, `adafruit-circuitpython-*` (VL53L0X, SCD4x), `ahrs` or a hand-rolled Madgwick filter, `numpy`, `httpx`.
- **Memory budget (1GB):** video ring buffers about 35MB, sensor buffers under 10MB, Python about 150MB. No desktop.
- **Device auth:** a long-lived device token, exchanged for short-lived signed upload URLs.
- **Demo tooling:**
  - `argus-cli trigger <type>` fires a synthetic event with a real clip;
  - `argus-cli replay <file>` replays a recording;
  - `argus-cli buzz <top|back|both>` tests the motors.

---

## 6. Backend & dashboard

### 6.1 Stack (recommended)
- **Next.js (App Router, TypeScript)** for the dashboard and API routes, deployed on **Vercel**.
- **Supabase** for Postgres, Storage (clips, sensor traces) and Realtime (the incident list updates live during the pitch).
- **Report worker:** a Node worker (or Supabase Edge Function) triggered by a DB insert or queue. LLM calls can take more than 60s, so set long function timeouts. If the platform limits bite, run the worker as a small long-running process on a team laptop during the demo.
- **SDKs:** Google Gen AI SDK for Gemini, `@anthropic-ai/sdk` for Claude. Use structured outputs for the report JSON. Enable server-side refusal fallbacks (`fallbacks: "default"`) on the Opus call.
- **Video:** the backend transcodes the rear MJPEG `.avi` to MP4 (ffmpeg) for browser playback. The front clip is already MP4.

Why this stack: the team knows TypeScript/React, it gets to a polished UI fastest, and Realtime with Storage comes free with it. The Pi side stays Python.

### 6.2 Data model (multi-tenant)

```
organizations(id, name)
sites(id, org_id, name, address, province)
crews(id, site_id, name, supervisor_name)
workers(id, crew_id, full_name, trade, employee_no)
devices(id, org_id, serial, token_hash, last_event_at)          -- no heartbeat; last_event_at updates on each upload
device_assignments(id, device_id, worker_id, starts_at, ends_at) -- hat ↔ worker; reassigning = close one row, open another
events(id, device_id, worker_id, site_id, type, severity, t0, detected_at,
       device_class, llm_suggested_class, llm_reclass_reason, final_class,   -- §3.4
       peak_g, closing_rate_mps, ttc_s, sensor_id, hat_on,
       co2_ppm, temp_c, rh_pct, status)          -- status: uploading|analyzing|ready|failed
media(id, event_id, kind, storage_path, duration_s, camera)   -- kind: clip|sensor_trace|thumbnail
analyses(id, event_id, stage, model, input_tokens, output_tokens, cost_usd, latency_ms, output_json)
reports(id, event_id, llm_draft_json, report_json, edited, status, signed_off_by, signed_off_at)
       -- llm_draft_json is immutable; report_json is the manager-edited copy; status: draft|signed
       -- signed = locked. No corrective-action tracking table (decided: actions stay as text in the report)
```

- `worker_id` on an event comes from the `device_assignments` row active at `t0`, so reports name the worker.
- Seed data: 1 org, 1 site (Edmonton-area example project), 3 crews, about 40 workers, about 8 devices (1 real, 7 seeded), and a handful of historical events with reports, so the incident list looks like a live site.
- Every LLM call writes a row to `analyses` with tokens, cost and latency. That gives cost-per-event figures for the pitch and makes budget tracking easy.

### 6.3 Report format: Canada-generic incident / near-miss investigation

An OHS-style structure common across Canadian EHS practice. **It is not a compliance filing.** The report carries a fixed footer: *"AI-generated draft for review by a competent person. Does not replace provincial OHS reporting or your employer's incident-reporting procedure."*

Report JSON schema (enforced via structured outputs):

1. **Header:** report no., classification (device class per §3.4, plus an optional LLM `suggested_class` and reason), date/time, site, location on site (always the fixed text **"Location to be determined"**, filled in by the manager at sign-off if known. The LLM is told not to infer location from the video), worker, crew, supervisor, device.
2. **Summary:** 2–3 sentences in plain language.
3. **Sequence of events:** a timestamped list that merges the sensor timeline and the video observations, with each item tagged `source: sensor | video | inferred`.
4. **Evidence:**
   - clip references with timestamps,
   - key sensor values (peak g, closing rate, TTC, sensor direction, hat-on state),
   - environmental conditions (SCD41).
5. **Hazard identification:** hazard category (struck-by, caught-between, fall from height, falling object, PPE, …), energy source.
6. **Risk assessment:** potential severity (1–5) × likelihood (1–5) on a 5×5 risk matrix, with a one-line justification for each.
7. **Immediate causes** (substandard acts / conditions) and **root / basic causes**, as a short 5-Whys chain.
8. **Corrective & preventive actions:** a text list, each tagged with a **hierarchy-of-controls** level (Elimination / Substitution / Engineering / Administrative / PPE) and a suggested owner role. **Not tracked** (no owners, due dates or open/closed status in the app; decided).
9. **Regulatory note:** flags whether the event *may* meet a provincial "serious incident" or "dangerous occurrence" notification threshold → *"verify with your provincial OHS regulator"*. Never asserts compliance.
10. **Confidence & gaps:** what the model could not determine from the evidence.
11. **Sign-off block** (filled in by a human in the UI).

**Review flow (decided):** the LLM output is always a **DRAFT**. The manager can edit any field; the original LLM draft is kept unchanged (`llm_draft_json`). Edited fields show an "edited by manager" marker. Sign-off records name and timestamp, accepts or rejects any reclassification suggestion, and locks the report.

### 6.4 Dashboard: incident detail + report (the pitch screen)

Primary user: the **site safety manager**. Visual language should follow the incident modules in widely used construction safety platforms (Procore Incidents, SafetyCulture, HammerTech-style layouts): a dense, form-like report with status chips and an audit trail. **Use our own branding and components. Do not copy any vendor's look.**

Layout (desktop first; it must still render on a tablet):
- **Header bar:**
  - incident no., classification chip, severity chip (color-coded from the risk matrix), status (Draft / Signed)
  - if the LLM suggested a reclassification: a banner "AI suggests: Incident — video shows contact [Accept] [Reject]"
  - worker, crew, site, date/time
  - "Export PDF" button and "Sign off" button
- **Left column (evidence):**
  - **Video player** (front/rear tabs) with event markers on the scrubber (t0 = trigger).
  - **Sensor chart synced to the video playhead.** Panels: |a| (g), per-sensor range and closing rate, hat-on band. Scrubbing either the video or the chart moves both.
  - Environmental readings strip (CO₂, temp, RH).
- **Right column (report):** the §6.3 sections as editable form fields. AI-generated text is visually marked until a human edits or accepts it. Edited fields show "edited by manager" (the original is viewable). Corrective actions are a plain text list.
- **Processing state:** while the event is `uploading → analyzing`, show a live progress stepper (Clip received → Video analyzed → Report drafted). It is visible during the pitch, as the 5-minute countdown plays out.
- **Home KPI row** (above the incident list): near misses this week · incidents this week · reports awaiting sign-off · per device "last event seen" time (no heartbeat, so there is no live online/battery status).
- **New-event toast:** Supabase Realtime pushes a toast the moment an event row lands ("Struck-by near miss — Hat #1 / J. Smith — report generating…"). No SMS or email.
- **Incident list** (minimal): a table of events with type, severity, worker, time and status that updates live via Realtime. Clicking a row opens the detail view.
- **PDF export:** a print-styled version of the report for handing to a superintendent or JHSC.

---

## 7. Pitch demo runbook

Target: trigger an event at the start of the pitch and present the finished report by the end (about 5 min).

1. **Pre-flight (T−30 min):**
   - Pi on venue Wi-Fi (assumed reliable; no hotspot fallback, decided).
   - Boot the hat resting on the table for calibration (§4.3), then put it on.
   - `i2cdetect` shows all devices.
   - Camera preview OK; haptics test (`argus-cli buzz both`).
   - Dashboard open on the incident list.
2. **Live struck-by:** a teammate swings a foam board toward the wearer from behind. The back motor buzzes and the event appears on the dashboard as "uploading".
3. **Drop / impact:** drop the hat about 1m onto a mat → `fall`/`head_impact` event.
4. **Hat-off:** take the hat off and set it down → `hat_removed` PPE event within about 3s.
5. While talking, show the progress stepper. At the end, open the struck-by incident: video synced with the sensor chart, plus the full report and corrective actions.
6. **Backups:**
   - a pre-recorded event with a completed report already in the DB;
   - `argus-cli trigger` if the sensors misbehave on stage.
- **Haptics on stage:** the wearer narrates ("I just felt the back motor"). No on-screen mirror or LED.

---

## 8. Build order

Two tracks run **in parallel** and meet at step 5.

**Track H: hardware team (now):**
- H1. Test each sensor with standalone scripts:
  - MPU-6050: ±16g / ±1000°/s / DLPF 260Hz / FIFO at 1kHz, and confirm no FIFO overflow over a 10-minute run;
  - I2C devices on one bus (`i2cdetect -y 1`);
  - VL53L0X bench test at 30/50/80mm → design the recessed mount;
  - L293D + motors buzz from Pi PWM. **Decide the Arduino fallback here**;
  - **beam/range test (§3.2)**, then 5× HC-SR04 interleaved sweep with dividers.
- H2. Paste each working script using the hand-off template (§5.5). Claude wraps it as a `team` impl.
- H3. Record real sessions (`argus-cli record`) for replay tests: walking backward, standing up, board swings, drops.

**Track S: Claude (software, starting once this spec is approved):**
1. `contracts.py`, bus, supervisor with hot-swap/fallback, `mock` impls + scenarios, contract test suite, CLI.
2. Detectors (L2) + event manager/haptic patterns (L3), all tested against scenario mocks.
3. Backend skeleton: Supabase schema + seed, signed upload, event ingest, incident list with Realtime.
4. LLM stage 1 (Gemini) → temporary report (option A). **End-to-end demo works on mocks.**
5. **Merge point:** wrap the team scripts as `team` impls (§5.5). Write the `argus` drivers. Run contract tests and shadow mode on real hardware. Pick `impl`/`fallback` per module.
6. Camera ring buffer + clip writer on the real Pi Cam.
7. Incident detail UI: video + synced chart + report form.
8. LLM stage 2 (Claude Opus 5.5) with the full report schema.
9. Ego-motion tuning + false-positive tuning: record labelled trials, run `argus-cli sweep` (§5.6), lock thresholds in `config.yaml`, keep replay regression green.
10. Rear USB camera (phase 2), PDF export, polish.
11. Demo rehearsal ×3 with the runbook (§7), including one run with a forced module fallback.

---

## 9. Open questions

- VL53L0X near-limit: confirm in the ST datasheet. Bench-test the reading at 30, 50 and 80mm against hair.
- Whether the Anker pack triggers Pi under-voltage throttling under full load (`vcgencmd get_throttled`).
- Exact Gemini model name and current video token rate. Re-verify at signup.
- Whether the front camera should face forward or down at the hands (test both on day 2).
- Haptic pattern legibility: user-test with 3 people whether they can tell top from back pulses through padding.
