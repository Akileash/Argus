# team/imu: plug in your script

Status: **not configured yet.** Until `mapping.yaml` has a `command` (or `python`), `create()` raises
`NotImplementedError("team impl for imu not pasted yet")` and the supervisor falls back (spec section 5.4).

## Steps (spec section 5.5)

1. Copy your script into `vendor/`. Do not change it. Everything of yours lives in this folder, so your
   branch cannot collide with anyone else's.
2. Make a starter mapping: `cd device && uv run argus-cli team init imu --format json` (or `--format text`).
3. Open `mapping.yaml` and fill in the `command`, the field names and the units (examples below).
4. Paste 10-20 lines of your script's real output into `sample.txt` and run
   `uv run argus-cli team check imu --sample sample.txt`. It prints every reading in contract units, or the
   exact error for a bad line. No hardware needed. Repeat until it is clean.
5. On the Pi with the hardware attached, run the contract tests:
   `uv run pytest tests/contract --module imu --impl team`
6. Turn it on for your machine in `device/config.local.yaml` (gitignored; never edit `config.yaml` for this):

   ```yaml
   modules:
     imu: { impl: team, fallback: mock }
   ```

   then `uv run argus-cli reload imu` on a running device (or just start it).
7. Send the integrator the hand-off template below plus your `sample.txt`, then open a pull request that
   touches only `device/hardware/team/imu/` (see `CONTRIBUTING.md`).

Hand-off template:

```
Module: imu
Wiring: pins / I2C address / power used
Libraries + versions: (pip freeze excerpt)
What it outputs: 10-20 lines of real output, pasted as is
Units + rate: e.g. "distance in cm, ~10 readings/s"
Known quirks: e.g. "sensor 3 reads 0 sometimes", "needs 2s warmup"
```

## What the module must deliver

accel in **g** (a still hat reads about 1 g in total), gyro in **deg/s**, hat frame x forward / y left / z up, at least 400 readings per second. Timestamps are stamped by the adapter; yours are ignored.

## Example: the script prints JSON, one object per line

Output:

```
{"ts": 1794079772.6, "accel": {"x": 0.294, "y": 0.196, "z": 9.807}, "gyro_rads": [0.035, 0.017, 0.009]}
```

`mapping.yaml`:

```yaml
command: [python, vendor/imu_test.py]
format: json
fields: { ax: accel.x, ay: accel.y, az: accel.z, gx: gyro_rads.0, gy: gyro_rads.1, gz: gyro_rads.2 }
accel_unit: m/s2             # m/s2 -> g
gyro_unit: rad/s             # rad/s -> deg/s
axes: { x: y, y: -x, z: z }  # hat x (forward) is your y; hat y (left) is minus your x
```

## Example: the script prints plain text

Output:

```
ax=0.01 ay=-0.00 az=1.01 gx=0.4 gy=-0.3 gz=0.2
```

`mapping.yaml`:

```yaml
command: [python, vendor/mpu_loop.py]
format: text                  # key=value pairs are found automatically
fields: { ax: ax, ay: ay, az: az, gx: gx, gy: gy, gz: gz }
accel_unit: g
gyro_unit: deg/s
```

A different text layout? Use `regex:` with named groups (one reading per match), or `columns: [a, b, c]`
with `delimiter: ","`. Lines that are just banners can be skipped with `ignore: '^Starting'`.

## Keys for imu

| Key | Meaning |
|---|---|
| `fields` | the name (or dotted path) of each of `ax ay az gx gy gz` in your output |
| `accel_unit` | `g`, `m/s2`, `mg`, `lsb:16384` (raw counts per g) or a number (multiplier) |
| `gyro_unit` | `deg/s`, `rad/s`, `mdps`, `lsb:131` (raw counts per deg/s) or a number |
| `axes` | hat axis = your axis with a sign, e.g. `{x: -y, y: x, z: z}`. Applies to accel and gyro |

Keys for every streaming module: `command` (a list, or one string), `format` (`json` or `text`),
`warmup_s` (ignore output for N s after a start), `ignore` (regex of lines to skip), `restart`
(`{initial_s: 0.5, max_s: 10}` backoff), and the same keys can sit under
`modules.imu.options` in `config.local.yaml`, where they win over `mapping.yaml`.

## Pattern (a): import and call instead of a subprocess

Instead of `command:`, give a Python module (inside `vendor/`) and the function to call in a loop:

```yaml
python: vendor.their_script:read_once   # returns a dict, a list, a tuple or a text line per call
setup: init_sensor                      # optional: called once at start
interval_s: 0.01                        # pause between calls
```

The function may also be a generator that yields readings. `format:` applies when it returns text.
Pattern (b), the default, is the subprocess: your blocking `while True: print(...)` script runs unchanged and
the adapter reads its stdout.

## What happens when it breaks

A malformed line is counted in `health().error_count` and skipped. If the script exits or crashes, `health()`
turns not-ok with the reason (and the last line it wrote to stderr), the supervisor falls back to the
`fallback` impl and records a `module_fallback` event, and the adapter restarts the script with backoff.
If the mapping cannot express your hardware, write a class in `adapter.py` (helpers in
`hardware/team/_adapter_base.py`) and return it from `create()`.

Rules the contract enforces: monotonic clock only, `read()` returns within 5 ms and drains everything since
the last call, never raise out of `read()`/`set()`, honest `health()`. See `argus/contracts.py`.
