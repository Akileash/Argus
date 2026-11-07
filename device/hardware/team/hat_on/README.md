# team/hat_on: plug in your script

Status: **not configured yet.** Until `mapping.yaml` has a `command` (or `python`), `create()` raises
`NotImplementedError("team impl for hat_on not pasted yet")` and the supervisor falls back (spec section 5.4).

## Steps (spec section 5.5)

1. Copy your script into `vendor/`. Do not change it. Everything of yours lives in this folder, so your
   branch cannot collide with anyone else's.
2. Make a starter mapping: `cd device && uv run argus-cli team init hat_on --format json` (or `--format text`).
3. Open `mapping.yaml` and fill in the `command`, the field names and the units (examples below).
4. Paste 10-20 lines of your script's real output into `sample.txt` and run
   `uv run argus-cli team check hat_on --sample sample.txt`. It prints every reading in contract units, or the
   exact error for a bad line. No hardware needed. Repeat until it is clean.
5. On the Pi with the hardware attached, run the contract tests:
   `uv run pytest tests/contract --module hat_on --impl team`
6. Turn it on for your machine in `device/config.local.yaml` (gitignored; never edit `config.yaml` for this):

   ```yaml
   modules:
     hat_on: { impl: team, fallback: mock }
   ```

   then `uv run argus-cli reload hat_on` on a running device (or just start it).
7. Send the integrator the hand-off template below plus your `sample.txt`, then open a pull request that
   touches only `device/hardware/team/hat_on/` (see `CONTRIBUTING.md`).

Hand-off template:

```
Module: hat_on
Wiring: pins / I2C address / power used
Libraries + versions: (pip freeze excerpt)
What it outputs: 10-20 lines of real output, pasted as is
Units + rate: e.g. "distance in cm, ~10 readings/s"
Known quirks: e.g. "sensor 3 reads 0 sometimes", "needs 2s warmup"
```

## What the module must deliver

**mm**; status `ok`, `out_of_range` or `error`. The VL53L0X reads about 8190 mm when nothing is in range. About 10 readings per second.

## Example: the script prints JSON, one object per line

Output:

```
{"distance_m": 0.063, "status": 0}
{"distance_m": 8.19, "status": 0}
```

`mapping.yaml`:

```yaml
command: [python, vendor/tof_loop.py]
format: json
fields: { range: distance_m, status: status }
unit: m                      # m -> mm
ok_values: [0]               # any other status value becomes "error"
max_mm: 2000                 # 8190 mm is >= this, so it becomes out_of_range
```

## Example: the script prints plain text

Output:

```
distance_mm=63 status=0
```

`mapping.yaml`:

```yaml
command: [python, vendor/tof_loop.py]
format: text
fields: { range: distance_mm, status: status }
unit: mm
ok_values: [0]
```

A different text layout? Use `regex:` with named groups (one reading per match), or `columns: [a, b, c]`
with `delimiter: ","`. Lines that are just banners can be skipped with `ignore: '^Starting'`.

## Keys for hat_on

| Key | Meaning |
|---|---|
| `fields.range` | the distance field. `fields.status` is optional |
| `unit` | `mm`, `cm`, `m` |
| `no_echo` | raw values meaning nothing in range (default `[-1]`) |
| `max_mm` | at or above this the reading is `out_of_range` (default 2000) |
| `ok_values` | status values that mean "good" (default `[0, ok]`); others become `error` |

Keys for every streaming module: `command` (a list, or one string), `format` (`json` or `text`),
`warmup_s` (ignore output for N s after a start), `ignore` (regex of lines to skip), `restart`
(`{initial_s: 0.5, max_s: 10}` backoff), and the same keys can sit under
`modules.hat_on.options` in `config.local.yaml`, where they win over `mapping.yaml`.

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
