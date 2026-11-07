# Swapping in your own sensor code

This guide is for the hardware team. It explains how the Pi software is built so that your tested
sensor scripts can replace ours with one line in a config file. It also explains what we need from
you and what we will do with it.

Read this before you send us a script.

---

## 1. Why the system is modular

The Pi runs six hardware modules. Each one has a fixed name:

| Module | What it reads or drives |
|---|---|
| `imu` | MPU-6050 (accelerometer + gyro) |
| `range` | the two rear HC-SR04 ultrasonic sensors (R2, R3) |
| `hat_on` | VL53L0X time-of-flight sensor in the crown |
| `env` | SCD41 CO2, temperature and humidity |
| `haptics` | the two vibration motors (top and back) through the L293D |
| `camera` | the Pi Camera, which keeps a rolling video buffer |

Every module has up to four **implementations** (the code that actually talks to the hardware):

| Implementation | What it is | Who uses it |
|---|---|---|
| `argus` | Our driver, written from the datasheets | the default, once it passes the bench checks |
| `team` | Your tested script, wrapped in a small adapter | the known-good fallback, or the default if you prefer it |
| `mock` | Fake sensor data from scenario scripts | laptops and CI, no hardware needed |
| `replay` | Plays back a recorded `.jsonl` log | tuning and regression tests |

The rest of the software (detection, events, uploads, the dashboard) never touches the hardware.
It only reads the common sample format. That is why swapping a module does not break anything else.

### Config: choose an implementation per module

Each module has an `impl` (use this first) and an optional `fallback` (use this if the first one
fails). Both are set in `config.yaml`. On the Pi the service reads `/etc/argus/config.yaml`.

```yaml
modules:
  imu:     { impl: argus, fallback: team }
  range:   { impl: team,  fallback: argus }
  hat_on:  { impl: argus }
  env:     { impl: argus }
  haptics: { impl: team }
  camera:  { impl: argus }
```

Leave out `fallback` if there is no backup. Then a failing module stays off until someone fixes it.

### Automatic fallback when health is bad

The supervisor (the part of the software that runs the modules) checks each module all the time.
It switches to the `fallback` on its own when any of these happen:

- the driver's `health()` says `ok = False`;
- no new samples arrive for too long: 500 ms for `imu` and `range`, 3 s for `hat_on`, 30 s for `env`,
  2 s for `camera`;
- `read()` raises an error;
- the haptics self-test fails. This runs every second and sets both motors to zero intensity.

When this happens:

- the switch is logged as an event named `module_fallback`, with the reason;
- the switch does **not** go back by itself. Fix the cause, then run `argus-cli reload <module>`.

---

## 2. Paste-your-script flow

### Step 1. Send us the hand-off template

Paste this into chat, one copy per module. Fill in every line.

```
Module: (imu | range | hat_on | env | haptics | camera)
Wiring: pins / I2C address / power used
Libraries + versions: (pip freeze excerpt)
What it outputs: example output lines (10–20 lines of real data)
Units + rate: e.g. "distance in cm, ~10 readings/s"
Known quirks: e.g. "sensor 3 reads 0 sometimes", "needs 2s warmup"
```

Also send the script itself. A script that is a blocking `while True: print(...)` loop is fine.
We do not need you to rewrite it.

### Step 2. What we will do with it

For each module, we will:

1. Copy your script into `device/hardware/team/<module>/` **without changing its hardware logic**.
   For the IMU, the file is `device/hardware/team/imu/their_script.py`.
2. Write `adapter.py` in the same folder. The adapter converts your output to our format:
   units, timestamps and sensor IDs. It runs your code either in a thread or as a subprocess that
   we read line by line. Both patterns are sketched in each module's `adapter.py`.
3. Run the contract tests against the adapter, with the hardware attached (see section 5).
4. Compare the wiring and constants in your script with our `argus` driver. If they disagree
   (pins, I2C speed, scale factors), we will flag it. **Your bench-proven values win.**
5. Set `impl` or `fallback` for the module, as you choose.

Until a script is pasted, the `team` implementation for that module raises an error on purpose.
The supervisor then uses the fallback, or leaves the module off. Nothing crashes.

---

## 3. How to choose: `impl` or `fallback`, per module

Use this as a starting point. Tell us your choice in the hand-off message.

| Situation | Set |
|---|---|
| Your script is proven and ours is not yet tested on the bench | `impl: team`, `fallback: argus` |
| Our driver is proven and yours is a backup | `impl: argus`, `fallback: team` |
| Both are proven and you want to compare | Run shadow mode first (section 6), then decide |
| Only one is available | `impl: <that one>`, no fallback |
| The module cannot be shadowed (haptics, camera) | Pick by A/B bench run (section 6) |

---

## 4. Hot-swap one module on the running device

You can change a module's implementation while the service is running. The other modules keep
running.

1. Edit the module's line in `/etc/argus/config.yaml`. For example, change `impl: argus` to
   `impl: team`.
2. Run:

   ```bash
   cd ~/argus/device
   .venv/bin/argus-cli reload imu
   ```

3. Expected output: a line like this.

   ```
   imu now on team (swapped in 120 ms)
   ```

   The swap takes about two seconds or less.

To try an implementation without editing the file, pass `--impl`:

```bash
.venv/bin/argus-cli reload imu --impl team
```

If you see `no running 'argus-cli run' found`, the service is not running. Start it with
`sudo systemctl start argus`, then try again.

If you see `reload failed: ...`, the old implementation is still running. Read the error, fix the
script or the config, and try again.

---

## 5. Contract tests: check one module with one implementation

The contract tests check that an implementation follows the rules in section 7. They run on the Pi
with the hardware attached.

From the `device/` folder:

```bash
cd ~/argus/device
uv run pytest tests/contract --module imu --impl team
```

- Change `imu` to any module name, and `team` to any implementation name.
- You can repeat `--module` to test several modules at once:
  `uv run pytest tests/contract --module imu --module range --impl team`
- Keep the hat still while `imu` runs. The test checks that the accelerometer reads about 1 g at rest.
- Expected at the end: `passed` with no `failed`.

> This command has only been run on `mock` so far, because no team script has been pasted yet.

Without hardware, the same command with `--impl mock` runs on a laptop:

```bash
uv run pytest tests/contract --module imu --impl mock
```

### Bench self-test (a human checks the readings)

The contract tests check the rules. The bench self-test shows live readings and asks a person to
check them:

```bash
.venv/bin/argus-cli test imu --impl team --config /etc/argus/config.yaml
```

Add `--interactive` to run the steps that need a person (for example, "tilt the hat 90 degrees").
Stop the service before you run bench tests (`sudo systemctl stop argus`). The bench tests open the
sensors themselves.

---

## 6. Shadow mode: compare two implementations on the same sensor

Shadow mode runs two implementations of the same module at the same time. It writes a log that
shows how far apart their readings are.

```bash
sudo systemctl stop argus
cd ~/argus/device
.venv/bin/argus-cli shadow imu --a argus --b team --seconds 10 --config /etc/argus/config.yaml
```

- `--a` is the first implementation. It defaults to the configured `impl`.
- `--b` is the second implementation. It defaults to the configured `fallback`.
- `--seconds` is how long to run. Default is 10.
- Add `--alternate` if the sensor cannot be shared. The two implementations then take turns, one
  second at a time. You get rates and counts, but no sample-by-sample comparison.
- Shadow mode only works for `imu`, `range`, `hat_on` and `env`. It does not work for `haptics` or
  `camera`. Those need an A/B bench run instead.

Expected output: the path of a `.jsonl` log file, then a short summary with sample counts and
average differences. The log is written to `device/var/shadow/`.

Start the service again when you are done: `sudo systemctl start argus`.

---

## 7. The module contract (what every implementation must send)

These are the rules from `device/argus/contracts.py`. Your adapter converts your output to these
units and fields.

### Shared rules for all implementations

- **Clock:** use `time.monotonic_ns()` only. Never use wall-clock time.
- **Non-blocking:** `read()` returns in 5 ms or less. Slow hardware code runs in its own thread.
- **`read()` drains everything** since the last call, oldest first.
- **Never raise** out of `read()` or `set()`. Log the problem, count the error, and return what you
  have.
- **Frame:** x = forward, y = left, z = up (the hat's own frame).
- Every implementation provides `start()`, `stop()` and `health()`.
- `health()` returns: `ok` (true or false), `last_sample_age_ms`, `error_count` and `note`.

### Per module

| Module | Method(s) | Sample fields and units | Expected rate | Staleness limit (fallback) |
|---|---|---|---|---|
| `imu` | `read() -> list[ImuSample]` | `t_ns`, `ax`, `ay`, `az` in **g**; `gx`, `gy`, `gz` in **deg/s** | at least **400 Hz** (our driver runs at about 1000 Hz) | 500 ms |
| `range` | `read() -> list[RangeSample]` | `t_ns`, `sensor_id` (`R2` or `R3` for the MVP; `R1`, `R4` and `TOP` stay valid for later), `range_mm` in **mm** or `None` for no echo | each sensor about **6.7 readings/s** (one full sweep about 150 ms, so about 33 samples/s in total) | 500 ms |
| `hat_on` | `read() -> list[TofSample]` | `t_ns`, `range_mm` in **mm** or `None`, `status` (one of `ok`, `out_of_range`, `error`) | about **10 Hz** | 3 s |
| `env` | `read() -> list[EnvSample]` | `t_ns`, `co2_ppm`, `temp_c`, `rh_pct` | about **0.2 Hz** (one reading every 5 s) | 30 s |
| `haptics` | `set(motor, intensity)`, `off_all()` | `motor` is `top` or `back`; `intensity` is 0.0 to 1.0 | no samples. Patterns are not in the driver. | self-test every 1 s |
| `camera` | `save_clip(t_start_ns, t_end_ns, path) -> ClipInfo` | `ClipInfo`: `path`, `t_start_ns`, `t_end_ns`, `duration_s`, `bytes`, `camera` (`front`) | keeps its own buffer of at least 30 s | 2 s |

Notes for your adapter:

- **`imu`:** if your script gives m/s² or rad/s, convert to g and deg/s. A stationary hat must read
  about 1 g in total. The contract test fails if the units are wrong.
- **`range`:** if your script gives cm, convert to mm. Use `None` for "no echo", not 0.
- **`haptics`:** the driver only has to turn a motor on at some strength, and turn it off again.
  Our pattern player (in the event code) decides when and how.
- **`camera`:** frames must be stamped with `time.monotonic_ns()`, not with the camera's own clock.

---

## 8. Quick reference: commands for the team

All commands run on the Pi, from `~/argus/device`, with the service stopped unless noted.

| Task | Command |
|---|---|
| Bus scan | `i2cdetect -y 1` |
| Bench self-test one module | `.venv/bin/argus-cli test <module> --impl <impl> --config /etc/argus/config.yaml` |
| Buzz the motors | `.venv/bin/argus-cli buzz both --config /etc/argus/config.yaml` |
| Contract tests | `uv run pytest tests/contract --module <module> --impl <impl>` |
| Shadow two implementations | `.venv/bin/argus-cli shadow <module> --a <impl> --b <impl>` |
| Hot-swap while running | `.venv/bin/argus-cli reload <module>` |

---

## 9. Questions to answer in your hand-off message

- Which module is this?
- Which impl do you want as the default, and which as the fallback?
- Did you test it with the hat on the head, or on the bench?
- Does your script need a warm-up time? If so, how long?
- Is there any pin or wiring that differs from the spec table in `.plans/argus-mvp-spec.md` section 2.3?
