# team/range: plug in your script

Status: **not configured yet.** Until `mapping.yaml` has a `command` (or `python`), `create()` raises
`NotImplementedError("team impl for range not pasted yet")` and the supervisor falls back (spec section 5.4).

## Steps (spec section 5.5)

1. Copy your script into `vendor/`. Do not change it. Everything of yours lives in this folder, so your
   branch cannot collide with anyone else's.
2. Make a starter mapping: `cd device && uv run argus-cli team init range --format json` (or `--format text`).
3. Open `mapping.yaml` and fill in the `command`, the field names and the units (examples below).
4. Paste 10-20 lines of your script's real output into `sample.txt` and run
   `uv run argus-cli team check range --sample sample.txt`. It prints every reading in contract units, or the
   exact error for a bad line. No hardware needed. Repeat until it is clean.
5. On the Pi with the hardware attached, run the contract tests:
   `uv run pytest tests/contract --module range --impl team`
6. Turn it on for your machine in `device/config.local.yaml` (gitignored; never edit `config.yaml` for this):

   ```yaml
   modules:
     range: { impl: team, fallback: mock }
   ```

   then `uv run argus-cli reload range` on a running device (or just start it).
7. Send the integrator the hand-off template below plus your `sample.txt`, then open a pull request that
   touches only `device/hardware/team/range/` (see `CONTRIBUTING.md`).

Hand-off template:

```
Module: range
Wiring: pins / I2C address / power used
Libraries + versions: (pip freeze excerpt)
What it outputs: 10-20 lines of real output, pasted as is
Units + rate: e.g. "distance in cm, ~10 readings/s"
Known quirks: e.g. "sensor 3 reads 0 sometimes", "needs 2s warmup"
```

## What the module must deliver

**mm** (cm times 10), `None` for no echo, sensor ids **R2** and **R3** for the MVP (spec section 3.2a; R1, R4 and TOP stay valid for later). About 33 readings per second in total.

## Example: the script prints JSON, one object per line

Output:

```
{"sensor": "back_left", "dist_cm": 14.8}
{"sensor": "back_right", "dist_cm": 0}
```

`mapping.yaml`:

```yaml
command: [python, vendor/ultrasonic.py]
format: json
id: sensor                   # the field holding the sensor name ...
value: dist_cm               # ... and the field holding the distance
ids: { back_left: R2, back_right: R3 }
unit: cm                     # cm -> mm
no_echo: [0, -1]             # 0 and -1 mean "no echo" (null and "timeout" always do)
```

## Example: the script prints plain text

Output:

```
R2=148.0 R3=0
```

`mapping.yaml`:

```yaml
command: [python, vendor/ultrasonic.py]
format: text
sensors: { R2: R2, R3: R3 }  # R2 is the field called "R2" in your output
unit: cm
no_echo: [0, -1]
```

A different text layout? Use `regex:` with named groups (one reading per match), or `columns: [a, b, c]`
with `delimiter: ","`. Lines that are just banners can be skipped with `ignore: '^Starting'`.

## Keys for range

| Key | Meaning |
|---|---|
| `sensors` | one line carries every sensor: `{R2: <field>, R3: <field>}` (a sensor missing from a line is skipped) |
| `id` + `value` | one record per sensor: the field with its name, and the field with the distance |
| `ids` | your sensor names to ours: `{back_left: R2}` (a name that is not mapped is an error line) |
| `unit` | `mm`, `cm`, `m`, `in` |
| `no_echo` | raw values meaning "no echo" (default `[0, -1]`); `null`, `nan` and `timeout` always do |
| `valid_mm` | optional `[min, max]`; readings outside become "no echo" |

Keys for every streaming module: `command` (a list, or one string), `format` (`json` or `text`),
`warmup_s` (ignore output for N s after a start), `ignore` (regex of lines to skip), `restart`
(`{initial_s: 0.5, max_s: 10}` backoff), and the same keys can sit under
`modules.range.options` in `config.local.yaml`, where they win over `mapping.yaml`.

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
