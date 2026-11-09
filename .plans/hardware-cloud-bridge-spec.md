# Hardware ↔ cloud integration (cloud bridge) — spec

Written 2026-11-08 from the interview with Dip. Demo: Mon Nov 9, natHacks.

## 1. Goal

Get the teammates' **proven** hard-hat code (`hardware` branch, `device/pi_bringup/`) talking to the hosted cloud
(`https://try-argus.vercel.app` + Supabase) **without changing how it drives the sensors**.

Events, clips and context from the real hat must reach the dashboard and get an AI report, exactly like the
simulated hat does today.

## 2. Hard rules

1. **Their driver and detection code is the source of truth and is not edited.** This covers everything in
   `device/pi_bringup/` except the one approved config line in §5.2. The covered parts are:
   - `hat/` (IMU, ultrasonic, ToF, haptics, camera, `main.py`, `events.py`);
   - `pi_sensors/`;
   - the root scripts.
2. **Their docs are not edited** (`docs/HARDWARE-DIFF.md`, `docs/PI-BRINGUP.md`, `docs/CONTRACT-BRIDGE.md`,
   `device/pi_bringup/*.md`). Ours link to them.
3. **Nothing opens a sensor a second time.**
   - Only `hat.main` owns I2C bus 3, the camera and the GPIO pins.
   - Only their `pi_sensors` owns the SCD41.
   - The bridge only reads files and console output.
4. **If the bridge fails, the hat still works:** it still buzzes and records. The bridge is a separate process.
5. **On the Pi, agents may install and run, but not edit.** They may:
   - install our package into its own folder and venv;
   - run their tests and `hat.main`;
   - read logs.

   They must not edit `~/argus` (their tree), `/boot`, system config or wiring. Agents report findings to the PM;
   the PM decides the next instruction.
6. **No secrets in chat, code or git:**
   - the device secret comes from pairing and lives in the bridge's config on the Pi;
   - the Pi SSH login uses a key.
7. **Only the new branch gets pushed.** `software`, `hardware` and `main` are not pushed to directly. Our
   local-only demo and hosting commits stay local.

## 3. Decisions (made in the interview)

| # | Topic | Decision |
|---|---|---|
| D1 | What runs on the hat | **Option B:** their `hat.main`, untouched, plus a new **cloud bridge** that reuses our pairing and store-and-forward uploader. Decision matrix score 50 vs 30 (ours + their drivers) vs 34 (both). |
| D2 | Wear gate | **Keep it.** The demo changes: Okitha wears the hat and does a controlled fall/impact. No hat-drop-on-a-table demo. |
| D3 | Event types | `fall` → `fall` (incident). `line_of_fire` → `struck_by_near_miss`. Add hat-off events from their `[tof] HELMET ON/OFF` console lines (D6). |
| D4 | Struck-by classification | Always a near miss. If a `fall` follows within 2 s, the fall is the incident and links to the near miss. The AI may suggest reclassification; the manager decides. |
| D5 | Clips for line of fire | Add `"line_of_fire"` to `CAM_CLIP_TYPES` in their `hat/config.py`. One line, flagged in the PR for teammate approval (§5.2). |
| D6 | Hat-off rule | Our existing rule: `HELMET OFF` within 2 s after a fall → `hat_knocked_off` (incident, shares the fall's clip). `HELMET OFF` with no fall → `hat_removed` (PPE log, no clip, no report). Their 3 s OFF dwell already filters blips. |
| D7 | Sensor chart | Use what their events contain now: the IMU snippet plus key numbers. Ask teammates in the PR whether a longer IMU/ultrasonic window is a quick change for them; the bridge uses it automatically if it appears. |
| D8 | Air data (SCD41) | The bridge attaches the latest air reading (CO₂, °C, RH), if ≤ 5 min old, to each event as environment context. No live streaming. |
| D9 | Branch / PR | Branch `integration/cloud-bridge` from `origin/hardware`, then a PR into `hardware` with integration issues labelled. Decision matrix score 50 vs 36 (from software) vs 39 (local only). |
| D10 | Agent models | Tiered: Haiku for inventory and diffs, Sonnet to build and test, Opus (PM) for review and decisions. |
| D11 | Pi access | SSH as the Pi user over Dhir's Tailscale network, with key login from this Mac. |
| D12 | Docs | Update our README, `argus_llm_context.md`, `docs/CONNECT-THE-HAT.md` and the spec/build plan to the real hardware and the bridge. |
| D13 | Updates to Dip | After each sensor is integrated and tested, a short update with any decisions made. |

## 4. Architecture

```
Pi (Raspberry Pi 4)
  ~/argus/                      THEIR tree (unchanged, except D5)
    hat.main  ── buzz, detect ──► events.jsonl (append)   clips/<event_id>.mp4
       │ stdout: "[tof] HELMET ON|OFF  mm=… near=…"
    pi_sensors (SCD41) ─────────► pi_sensors/logs/air_events.jsonl
                         ▲ reads only
  ~/argus-cloud/                OUR package (own venv), runs as systemd `argus-bridge`
    argus-cli bridge run
      1. starts hat.main as a child process (their venv python, cwd ~/argus) and reads its stdout
         → passes every line through to the journal unchanged, and parses [tof] lines
      2. tails events.jsonl from a saved offset (survives restarts; never re-sends)
      3. for fall / line_of_fire: waits up to 45 s for clips/<event_id>.mp4 to finish (size stable);
         .h264 only → remux a copy with ffmpeg; none → event goes up without video (sensor-only report)
      4. maps the event to contracts/event.schema.json (§6), builds trace.json from the IMU snippet
      5. hands it to OUR existing queue + uploader (pairing, signed upload, /complete, retries)
                         │ Wi-Fi
                         ▼
  try-argus.vercel.app (API + worker) + Supabase → dashboard
```

**Bridge location in the repo:**
- `device/argus/bridge/` (new), plus an `argus-cli bridge` command;
- a systemd unit `device/deploy/argus-bridge.service`;
- an install path that uses `~/argus-cloud`, so it can't collide with their `~/argus`.

If `hat.main` exits, the bridge restarts it with backoff and logs the exit. A bridge crash must not leave
`hat.main` orphaned holding the buses: run it as a child in its own process group, and stop it on bridge exit.

**Fallback mode:** `argus-cli bridge run --no-launch` only tails files, for when teammates start `hat.main`
themselves. Hat-off events are then off unless `--tof-log <file>` points to a file their stdout is piped to.

## 5. Changes to their tree

### 5.1 Nothing in drivers or detection.

### 5.2 One config line (D5)
`device/pi_bringup/hat/config.py`: `CAM_CLIP_TYPES = ("fall", "line_of_fire")`.
- Flag it in the PR description and with a label, and ask teammates to confirm.
- Before merging, check on the Pi that a line-of-fire clip doesn't block or delay the buzz. Their `_handle` buzzes
  first and captures asynchronously.
- Check camera behaviour when a second capture is requested while one is running (a near miss followed by a fall
  within 30 s).
- **If that breaks anything, revert the line** and line-of-fire events go up without video.

## 6. Field mapping (their event → our contract)

| Theirs | Ours | Notes |
|---|---|---|
| `event_id` | `event_id` | used as-is (idempotent) |
| `hat_id` (`hat-01`) | `device_serial` | from our pairing (ARG-0001), not from their field |
| `type: fall` | `type: fall`, `device_class: incident` | |
| `type: line_of_fire` | `type: struck_by_near_miss`, `device_class: near_miss` | D4 |
| `[tof] HELMET OFF` | `hat_knocked_off` / `hat_removed` | D6 |
| `timestamp` (local + offset) | `t0` / `detected_at` in UTC | |
| `sensor.peak_accel_g` | `metrics.peak_g` | |
| `sensor.freefall_ms` | `metrics.free_fall_ms` | |
| `sensor.distance_m`, `closing_speed_mps` | `metrics.range_m`, `closing_rate_mps` | |
| `side` left / right | `metrics.sensor_id` | mapping (e.g. left → R2) to be confirmed on the hat |
| `sensor.helmet_worn_at_onset`, `wear_on_fraction` | `metrics.hat_on` + detail | |
| `sensor.imu_trace` `[t_ms, ax, ay, az]` | `trace.json` IMU series | short; D7 |
| latest air sample | environment context | D8 |
| `clip_path` / `clips/<id>.mp4` | `media[]` (hash, size, duration) | |

Their IMU runs at about 200 Hz; the contract text asks ≥ 400 Hz. Report the real rate and don't fake it. If the
schema rejects it, raise it as an integration issue for the PM.

## 7. Work plan (per sensor, PM loop)

**Phase 0 — access (Dip/Dhir):**
1. Install Tailscale on this Mac and join Dhir's network, or have the Pi shared with Dip's account.
2. Generate an SSH key on this Mac and add the public key to the Pi.
3. Check with `ssh <user>@<pi-tailscale-ip> true`.

**Phase 1 — inventory (Haiku):**
- Pull `origin/hardware` into a fresh worktree on branch `integration/cloud-bridge`.
- Produce a per-sensor diff of their reality vs our software assumptions: IMU/fall, ultrasonic/LoF, ToF/hat-off,
  SCD41/air, haptics, camera.
- Include their sample JSONs vs our contract, field by field.

**Phase 2 — bridge build (Sonnet):** the bridge per §4 and §6, plus laptop tests that:
- replay their `samples/*.json` and `events.sample.jsonl`;
- simulate `[tof]` lines and the clip appearing late, or as `.h264`;
- cover offset persistence, no duplicate sends and `hat.main` restarts;
- check the contract (`scripts/check.sh` must pass).

**Phase 3 — per-sensor integration, in this order:**
1. IMU/fall + camera
2. ultrasonic/line of fire (+ D5)
3. ToF hat-off
4. SCD41 air
5. haptics (verify only: the buzz stays theirs)

For each sensor:
1. **Build/adjust (Sonnet):** mapping and tests for that sensor.
2. **On-Pi test (Sonnet over SSH, within the rules in §2):**
   - run their `pytest hat/tests` and their `validate_wear_gated_fall.py` (must still pass);
   - run the bridge, trigger the real sensor, and confirm the event shows on `try-argus.vercel.app` with a clip
     (where expected) and a report.
3. **Report back to the PM** with findings, logs and labelled issues.
4. **The PM decides** each issue (decision matrix when unclear) and sends the next instruction.
5. **Update Dip:** a short message after each sensor.

**Phase 4 — PR (Sonnet, PM review):**
- push only `integration/cloud-bridge`;
- open a PR into `hardware`;
- every integration issue is listed with a label, the PM's decision, and any question for teammates (D5 config line,
  D7 longer trace, sensor_id left/right mapping, IMU rate).

**Phase 5 — docs (Sonnet, PM review):**
- update README, `argus_llm_context.md`, `docs/CONNECT-THE-HAT.md` and the spec/build plan;
- the CONNECT-THE-HAT update covers the real wiring (link to their HARDWARE-DIFF), installing the bridge to
  `~/argus-cloud`, pairing with `try-argus.vercel.app`, `systemctl enable --now argus-bridge` and the new demo
  (worn fall, not a drop).

## 8. Issue labels for the PR

- `integration:mapping` — field or type differences
- `integration:contract` — the schema rejects a real value
- `integration:timing` — clip, buzz or latency
- `integration:hardware-truth` — our docs are wrong about the hardware
- `needs-teammate` — needs their decision or their change
- `decided` — PM decision recorded

## 9. Definition of done

1. On the real Pi, with `hat.main` and the bridge running:
   - a worn controlled fall appears on `try-argus.vercel.app` within about 15 s, with a 30 s clip, and gets an AI
     report;
   - a line-of-fire near miss buzzes and appears (with a clip, if D5 holds);
   - taking the hat off logs `hat_removed`;
   - knocking it off after a fall logs `hat_knocked_off`.
2. Pull the Wi-Fi for 1 minute during an event; it uploads after reconnecting with no duplicates.
3. Their tests still pass. `git diff origin/hardware -- device/pi_bringup` shows only the D5 line.
4. `scripts/check.sh` passes.
5. The PR is open into `hardware` with labelled issues; docs are updated; Dip has had a per-sensor update.

## 10. Risks

- Camera contention if two captures overlap (D5). Mitigated by the on-Pi test; revert the line if needed.
- Clock: the Pi must have correct time (NTP over Wi-Fi), otherwise `t0` is wrong.
- Tailscale access depends on Dhir. Without it, fall back to a script Dhir runs and returns the output from.
- Their console format changing would break the hat-off parsing. Pin the expected `[tof] HELMET` pattern in tests and
  warn loudly if it is never seen.
