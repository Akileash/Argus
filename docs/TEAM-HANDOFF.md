# Plugging in your own sensor code

This guide is for the hardware team. Your tested sensor scripts replace ours by writing a small
mapping file, not by rewriting anything. Read the short version first, then the sections you need.

## The short version (6 steps, about 15 minutes per module)

Run everything from the `device/` folder. Replace `range` with your module (`imu`, `range`, `hat_on`,
`env`, `haptics` or `camera`).

1. **Branch.** `git switch -c hw/range-script` (see `CONTRIBUTING.md`). You only ever edit
   `device/hardware/team/range/`.
2. **Drop in your script, unchanged.** Copy it to `device/hardware/team/range/vendor/`.
3. **Make a starter mapping.** `uv run argus-cli team init range --format text` (use `--format json` if
   your script prints JSON). This writes `device/hardware/team/range/mapping.yaml`.
4. **Fill in the mapping** (what to run, what your fields are called, your units). Examples are in
   sections 3 and 4.
5. **Check it without hardware.** Paste 10 to 20 lines of your script's real output into a file and run
   `uv run argus-cli team check range --sample sample.txt`. Every line is printed in our units, or you
   get the exact error. Repeat until there are no errors.
6. **Prove it on the hat.** On the Pi, with the hardware attached:
   `uv run pytest tests/contract --module range --impl team`. When it passes, send the integrator the
   message in section 5 and open a pull request.

To use your code on your own machine, create `device/config.local.yaml` (it is ignored by git):

```yaml
modules:
  range: { impl: team, fallback: mock }
```

Do not edit `device/config.yaml` for this. That file is shared, and editing it is how merge conflicts
start. The integrator decides later which implementation is the default for everyone.

---

## 1. The data formats (who sees what)

Your code never has to produce our internal format by hand. The adapter does that.

| Where the data goes | Format | Example |
|---|---|---|
| Your sensor code to our detectors | **Python objects** (`RangeSample`, `ImuSample`, `TofSample`, `EnvSample`), made by the adapter | `RangeSample(t_ns=..., sensor_id="R2", range_mm=1480.0)` |
| Recordings of sensor runs (for tuning and replay) | **`.jsonl`** files, one JSON object per line (`hardware/replay/FORMAT.md`) | `{"stream": "range", "t_ns": 1030000000, "sensor_id": "R2", "range_mm": null}` |
| An incident on the device (`event.json`, `trace.json`) | **JSON** (`contracts/event.schema.json`, `contracts/sensor_trace.schema.json`) | `event.json` next to `clip.mp4` and `trace.json` |
| Device to the web app (the API) | **JSON** over HTTPS (`contracts/api.md`) | `POST /api/events` |

Your script can print JSON, plain text, or be a Python function that returns numbers. The mapping file
turns any of these into Python objects in our units:

| Quantity | Our unit |
|---|---|
| Range (`range`, `hat_on`) | millimetres. `None` means "no echo" |
| Acceleration | g (a still hat reads about 1 g in total) |
| Rotation rate | degrees per second |
| Sensor ids | `R2` and `R3` (the two rear sensors). `R1`, `R4` and `TOP` stay valid for later |
| Axes | x forward, y left, z up (the hat's frame) |
| Time | the adapter stamps each reading with the shared monotonic clock when it arrives. **Timestamps in your output are ignored.** |

---

## 2. How the Pi software is organised

The Pi runs six hardware modules, each with a fixed name:

| Module | What it reads or drives |
|---|---|
| `imu` | MPU-6050 (accelerometer + gyro) |
| `range` | the two rear HC-SR04 ultrasonic sensors (R2, R3) |
| `hat_on` | VL53L0X time-of-flight sensor in the crown |
| `env` | SCD41 CO2, temperature and humidity |
| `haptics` | the two vibration motors (top and back) through the L293D |
| `camera` | the Pi Camera, which keeps a rolling video buffer |

Every module has up to four **implementations** (the code that talks to the hardware):

| Implementation | What it is | Who uses it |
|---|---|---|
| `argus` | Our driver, written from the datasheets | the default, once it passes the bench checks |
| `team` | Your tested script, run by a small shared adapter and your `mapping.yaml` | the known-good fallback, or the default if you prefer it |
| `mock` | Fake sensor data from scenario scripts | laptops and CI, no hardware needed |
| `replay` | Plays back a recorded `.jsonl` log | tuning and regression tests |

The rest of the software (detection, events, uploads, the dashboard) never touches the hardware. It only
reads the sample objects, so swapping a module does not break anything else.

### Config: which implementation, and where to set it

`impl` is used first. `fallback` is used if the first one fails. Leave out `fallback` if there is no
backup: a failing module then stays off until someone fixes it.

The config is read in this order (the first line that applies is used):

| Order | Source | Used when |
|---|---|---|
| 1 | the file named by `--config`, or by `$ARGUS_CONFIG` | you name one. It is used **as is**. The Pi service runs with `--config /etc/argus/config.yaml`. |
| 2 | `device/config.yaml` with **`device/config.local.yaml`** merged on top | no file is named (laptop, your own work). Keys in the local file win; nested keys merge. |

Put your machine's choices (`impl: team`, a serial port, pairing details) in `config.local.yaml`.
`argus-cli pair` with no `--config` writes there too, so a device token is never committed.
`argus-cli sweep --apply` still writes `config.yaml` (or the `--config` file), because tuned thresholds
belong to everybody.

### Automatic fallback when health is bad

The supervisor checks each module all the time and switches to `fallback` on its own when:

- the driver's `health()` says `ok = False`. For a team script this includes "the script crashed";
- no new samples arrive for too long: 500 ms for `imu` and `range`, 3 s for `hat_on`, 30 s for `env`,
  2 s for `camera`;
- `read()` raises an error;
- the haptics self-test fails. It runs every second and sets both motors to zero.

The switch is logged as a `module_fallback` event with the reason. It does **not** go back by itself.
Fix the cause, then run `argus-cli reload <module>`.

---

## 3. Example: a script that prints JSON

Your script prints one JSON object per line (a JSON array of readings per line also works):

```
{"sensor": "back_left", "dist_cm": 14.8}
{"sensor": "back_right", "dist_cm": 0}
```

`device/hardware/team/range/mapping.yaml`:

```yaml
command: [python, vendor/ultrasonic.py]   # runs in the module folder; "python" is the project's python
format: json
id: sensor                   # the field with the sensor name ...
value: dist_cm               # ... and the field with the distance
ids: { back_left: R2, back_right: R3 }
unit: cm                     # cm -> mm
no_echo: [0, -1]             # 0 and -1 mean "no echo" (null and "timeout" always do)
```

`argus-cli team check range --sample sample.txt` then prints:

```
line 1: range  R2  148.0 mm
line 2: range  R3  no echo
```

An IMU that prints m/s^2 and rad/s in its own axes (`device/tests/team/mappings/imu.yaml` is a tested
example):

```yaml
command: [python, vendor/imu_test.py]
format: json
fields: { ax: accel.x, ay: accel.y, az: accel.z, gx: gyro_rads.0, gy: gyro_rads.1, gz: gyro_rads.2 }
accel_unit: m/s2             # m/s2 -> g.   Also: g, mg, lsb:16384 (raw counts per g)
gyro_unit: rad/s             # rad/s -> deg/s.   Also: deg/s, mdps, lsb:131
axes: { x: y, y: -x, z: z }  # hat x (forward) is your y; hat y (left) is minus your x
```

Field names can be dotted paths into nested JSON (`accel.x`, `gyro_rads.0` for the first array item).

## 4. Example: a script that prints plain text

Your script prints:

```
R2=148.0 R3=0
```

`device/hardware/team/range/mapping.yaml`:

```yaml
command: [python, vendor/ultrasonic.py]
format: text                 # "name=number" pairs are found automatically
sensors: { R2: R2, R3: R3 }  # R2 is the field called "R2" in your output
unit: cm
no_echo: [0, -1]
```

Other text layouts:

| Your output looks like | Add to the mapping |
|---|---|
| `Front: 12.5 cm \| Back: 9 cm` | `regex: '(?P<id>\w+): (?P<v>[\d.]+) cm'` and `id: id`, `value: v` (one reading per match) |
| `0.01, 0.02, 1.0, 1, 2, 3` (fixed columns) | `delimiter: ","` and `columns: [ax, ay, az, gx, gy, gz]` |
| a banner line such as `Starting sensor...` | `ignore: '^Starting'` (these lines are skipped silently) |
| the sensor needs a few seconds to settle | `warmup_s: 2` (readings in the first 2 s after a start are dropped) |

Your script is a Python library with a function instead of a script? Use the import pattern (the
module folder's `README.md` shows it): `python: vendor.their_script:read_once`.

All keys for each module are in that module's `README.md`
(`device/hardware/team/<module>/README.md`). `haptics` and `camera` do not print readings, so they
work differently: see their READMEs. For haptics, three ways are supported: call your Python function,
write lines to your script's stdin, or write lines to the Arduino serial port (spec section 2.4).

---

## 5. What to send the integrator

Send one message per module. Do not skip the sample lines: they are what we test against.

```
Module: (imu | range | hat_on | env | haptics | camera)
Wiring: pins / I2C address / power used
Libraries + versions: (pip freeze excerpt)
What it outputs: 10-20 lines of real output, pasted as is (save them as sample.txt too)
Units + rate: e.g. "distance in cm, ~10 readings/s"
Known quirks: e.g. "sensor 3 reads 0 sometimes", "needs 2s warmup"
Branch: the name of your branch, and the output of: argus-cli team check <module> --sample sample.txt
Choice: impl: team or fallback: team?   Tested on the head or on the bench?
```

The integrator will compare your wiring and constants with our `argus` driver. If they disagree (pins,
I2C speed, scale factors), we flag it. **Your bench-proven values win.**

---

## 6. Troubleshooting

| What you see | Cause | Fix |
|---|---|---|
| `team impl for range not pasted yet` | `mapping.yaml` has no `command` or `python` yet | add one (step 4). The supervisor falls back in the meantime, on purpose. |
| `line 3: ERROR not JSON: Starting up...` | your script prints a banner | `ignore: '^Starting'` in the mapping |
| `field 'dist_cm' not found in {...}` | the field is named differently, or nested | use the exact name; use dotted paths for nesting |
| `sensor 'front' is not mapped to R1..R4/TOP` | `ids:` does not list that sensor name | add `front: R2` (or whichever it is) under `ids:` |
| `median \|accel\| is 9.80 g` | your accel is in m/s^2 | `accel_unit: m/s2` (raw counts: `lsb:16384`) |
| the hat reads 1 g but tilts the wrong way | axes differ | set `axes:`, for example `{ x: y, y: -x, z: z }` |
| ranges are 10 times too small or large | cm versus mm | `unit: cm` |
| `no_echo` values show up as real distances | your "no echo" value is not listed | add it: `no_echo: [0, -1, 999]` |
| contract test: `no samples received` | the command prints nothing, or only partly | run `python vendor/your_script.py` by hand; check `command:` paths (they start in the module folder) |
| contract test: rate too low | the script is slow or buffers its output | print with `flush=True` (or `python -u`); check the rate you told us |
| `exited with code 1 right after start` (in `argus-cli status` or the fallback event) | the script crashed on start; the last stderr line is in the message | run it by hand on the Pi; check wiring and permissions (`i2c`, `gpio` groups) |
| `module_fallback` event, reason `stale` | no readings for longer than the limit in section 2 | the script hangs or prints too rarely; the unit may also be wrong, so no line parses |
| `mapping problem: ...` or `unknown key 'no_eco' (did you mean 'no_echo'?)` | typo in `mapping.yaml` | fix the key; `team check` names it |
| `reload failed: ...` | the new impl did not start | read the full message: it says whether the old impl is still running |
| `no running 'argus-cli run' found` | the service is not running | `sudo systemctl start argus` (Pi) or `uv run argus-cli run` (laptop) |
| `git merge` says `CONFLICT` in `pnpm-lock.yaml` or `device/uv.lock` | two people changed the generated lockfiles | `scripts/fix-lockfiles.sh` (see `CONTRIBUTING.md`) |

---

## 7. Hot-swap one module on the running device

You can change a module's implementation while the service runs. The other modules keep running.

1. Change the module in `/etc/argus/config.yaml` on the Pi (or in `config.local.yaml` on a laptop), for
   example `impl: team`.
2. Run:

   ```bash
   cd ~/argus/device
   .venv/bin/argus-cli reload imu
   ```

3. Expected output, a line like this:

   ```
   imu now on team (swapped in 120 ms)
   ```

   `reload` re-reads the config files **including `options`**, so a new mapping takes effect too. To try an
   implementation without editing the file, pass `--impl`:

   ```bash
   .venv/bin/argus-cli reload imu --impl team
   ```

**Two kinds of module.** `imu`, `hat_on` and `env` share the I2C bus, so the new implementation starts
first and the old one stops after it: no gap in the data. `range`, `haptics` and `camera` own GPIO pins
or the camera, which only one program can hold. For these the old implementation is **stopped first**,
then the new one starts and its health is checked. That means a short gap (no range samples, no camera
frames, motors cannot buzz) while the swap runs, normally well under two seconds.

If you see `reload failed: ...`, read the whole message:

- For `imu`, `hat_on` and `env` the old implementation never stopped. It is still running.
- For `range`, `haptics` and `camera` the old implementation was stopped to free the hardware. The message
  says either `the previous impl X was restarted and is running again` or `the previous impl X could NOT be
  restarted` (the module is down: fix the cause, then run `argus-cli reload <module>` again).

---

## 8. Contract tests

The contract tests check that an implementation follows the rules in section 10. From the `device/` folder:

```bash
uv run pytest tests/contract --module imu --impl team
```

- Change `imu` to any module name. Repeat `--module` to test several at once.
- This uses the module's `mapping.yaml` and runs your real script, so run it on the Pi with the hardware
  attached. Keep the hat still while `imu` runs: it checks that the accelerometer reads about 1 g.
- Expected at the end: `passed` with no `failed`.

You can try the whole flow on a laptop with the example fake teammates in `device/tests/team/`:

```bash
uv run pytest tests/contract --module imu --impl team --team-mapping tests/team/mappings -q
```

`--team-mapping` points the adapter at another mapping file, or at a folder holding `<module>.yaml`.

Without hardware and without any team script, the same suite runs on mocks:
`uv run pytest tests/contract --module imu --impl mock`.

### Bench self-test (a human checks the readings)

```bash
.venv/bin/argus-cli test imu --impl team --config /etc/argus/config.yaml
```

Add `--interactive` for steps that need a person (for example, "tilt the hat 90 degrees"). Stop the
service first (`sudo systemctl stop argus`): the bench tests open the sensors themselves.

---

## 9. Shadow mode: compare two implementations on the same sensor

Shadow mode runs two implementations at the same time and writes a log of how far apart they are.

```bash
sudo systemctl stop argus
cd ~/argus/device
.venv/bin/argus-cli shadow imu --a argus --b team --seconds 10 --config /etc/argus/config.yaml
```

- `--a` defaults to the configured `impl`, `--b` to the configured `fallback`.
- Add `--alternate` if the sensor cannot be shared: the two take turns, one second each. You get rates
  and counts, but no sample-by-sample comparison.
- It works for `imu`, `range`, `hat_on` and `env` only. For `haptics` and `camera`, use an A/B bench run.

Expected output: the path of a `.jsonl` log in `device/var/shadow/`, then a short summary. Start the
service again afterwards: `sudo systemctl start argus`.

### How to choose: `impl` or `fallback`, per module

| Situation | Set |
|---|---|
| Your script is proven and ours is not yet tested on the bench | `impl: team`, `fallback: argus` |
| Our driver is proven and yours is a backup | `impl: argus`, `fallback: team` |
| Both are proven and you want to compare | run shadow mode first, then decide |
| Only one is available | `impl: <that one>`, no fallback |

---

## 10. The module contract (what every implementation delivers)

From `device/argus/contracts.py`. The adapter converts your output to this.

- **Clock:** `time.monotonic_ns()` only. Never wall-clock time.
- **Non-blocking:** `read()` returns in 5 ms or less. Slow hardware code runs in its own thread or process.
- **`read()` drains** everything since the last call, oldest first.
- **Never raise** out of `read()` or `set()`. Count the error and return what you have.
- Every implementation has `start()`, `stop()` and `health()` (`ok`, `last_sample_age_ms`, `error_count`, `note`).

| Module | Method(s) | Sample fields and units | Rate | Staleness limit |
|---|---|---|---|---|
| `imu` | `read() -> list[ImuSample]` | `t_ns`, `ax ay az` in **g**, `gx gy gz` in **deg/s** | at least **400 Hz** | 500 ms |
| `range` | `read() -> list[RangeSample]` | `t_ns`, `sensor_id` (`R2`/`R3`), `range_mm` in **mm** or `None` | about **33 samples/s** in total (each sensor about 16.7/s) | 500 ms |
| `hat_on` | `read() -> list[TofSample]` | `t_ns`, `range_mm` in **mm** or `None`, `status` (`ok`, `out_of_range`, `error`) | about **10 Hz** | 3 s |
| `env` | `read() -> list[EnvSample]` | `t_ns`, `co2_ppm`, `temp_c`, `rh_pct` | about **0.2 Hz** | 30 s |
| `haptics` | `set(motor, intensity)`, `off_all()` | `motor` is `top` or `back`; `intensity` 0.0 to 1.0 | none. Patterns live in the event code. | self-test every 1 s |
| `camera` | `save_clip(t_start_ns, t_end_ns, path) -> ClipInfo` | `ClipInfo`: `path`, `t_start_ns`, `t_end_ns`, `duration_s`, `bytes`, `camera` | keeps at least 30 s | 2 s |

---

## 11. Quick reference

Laptop commands run from `device/`. Pi commands run from `~/argus/device`, with the service stopped
unless noted.

| Task | Command |
|---|---|
| Make a starter mapping | `uv run argus-cli team init <module> --format json` (or `text`) |
| Check pasted output, no hardware | `uv run argus-cli team check <module> --sample sample.txt` |
| Contract tests | `uv run pytest tests/contract --module <module> --impl team` |
| Hot-swap while running | `.venv/bin/argus-cli reload <module>` |
| Bench self-test one module | `.venv/bin/argus-cli test <module> --impl <impl> --config /etc/argus/config.yaml` |
| Buzz the motors | `.venv/bin/argus-cli buzz both --config /etc/argus/config.yaml` |
| Shadow two implementations | `.venv/bin/argus-cli shadow <module> --a <impl> --b <impl>` |
| Bus scan | `i2cdetect -y 1` |
| Everything, before you push | `scripts/check.sh` (hardware only, no Node: `scripts/check.sh --device`) |

## 12. Questions to answer in your message

- Which module is this?
- Which impl do you want as the default, and which as the fallback?
- Did you test it with the hat on the head, or on the bench?
- Does your script need a warm-up time? If so, how long?
- Is there any pin or wiring that differs from the spec table in `.plans/argus-mvp-spec.md` section 2.3?
