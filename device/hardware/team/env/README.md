# team/env: plug in your script

Status: **not configured yet.** Until `mapping.yaml` has a `command` (or `python`), `create()` raises
`NotImplementedError("team impl for env not pasted yet")` and the supervisor falls back (spec section 5.4).

## Steps (spec section 5.5)

1. Copy your script into `vendor/`. Do not change it. Everything of yours lives in this folder, so your
   branch cannot collide with anyone else's.
2. Make a starter mapping: `cd device && uv run argus-cli team init env --format json` (or `--format text`).
3. Open `mapping.yaml` and fill in the `command`, the field names and the units (examples below).
4. Paste 10-20 lines of your script's real output into `sample.txt` and run
   `uv run argus-cli team check env --sample sample.txt`. It prints every reading in contract units, or the
   exact error for a bad line. No hardware needed. Repeat until it is clean.
5. On the Pi with the hardware attached, run the contract tests:
   `uv run pytest tests/contract --module env --impl team`
6. Turn it on for your machine in `device/config.local.yaml` (gitignored; never edit `config.yaml` for this):

   ```yaml
   modules:
     env: { impl: team, fallback: mock }
   ```

   then `uv run argus-cli reload env` on a running device (or just start it).
7. Send the integrator the hand-off template below plus your `sample.txt`, then open a pull request that
   touches only `device/hardware/team/env/` (see `CONTRIBUTING.md`).

Hand-off template:

```
Module: env
Wiring: pins / I2C address / power used
Libraries + versions: (pip freeze excerpt)
What it outputs: 10-20 lines of real output, pasted as is
Units + rate: e.g. "distance in cm, ~10 readings/s"
Known quirks: e.g. "sensor 3 reads 0 sometimes", "needs 2s warmup"
```

## What the module must deliver

co2 in **ppm**, temperature in **deg C**, humidity in **%**; about one reading every 5 s.

## Example: the script prints JSON, one object per line

Output:

```
{"co2": 655, "temperature_f": 71.6, "humidity": 0.412}
```

`mapping.yaml`:

```yaml
command: [python, vendor/scd41_loop.py]
format: json
fields: { co2_ppm: co2, temp_c: temperature_f, rh_pct: humidity }
temp_unit: f                 # f -> deg C
rh_unit: fraction            # 0.412 -> 41.2 %
```

## Example: the script prints plain text

Output:

```
co2=655 temp=22.3 rh=41.2
```

`mapping.yaml`:

```yaml
command: [python, vendor/scd41_loop.py]
format: text
fields: { co2_ppm: co2, temp_c: temp, rh_pct: rh }
```

A different text layout? Use `regex:` with named groups (one reading per match), or `columns: [a, b, c]`
with `delimiter: ","`. Lines that are just banners can be skipped with `ignore: '^Starting'`.

## Keys for env

| Key | Meaning |
|---|---|
| `fields` | the name of each of `co2_ppm`, `temp_c`, `rh_pct` in your output (any may be left out) |
| `temp_unit` | `c`, `f`, `k` |
| `rh_unit` | `pct` or `fraction` |

Keys for every streaming module: `command` (a list, or one string), `format` (`json` or `text`),
`warmup_s` (ignore output for N s after a start), `ignore` (regex of lines to skip), `restart`
(`{initial_s: 0.5, max_s: 10}` backoff), and the same keys can sit under
`modules.env.options` in `config.local.yaml`, where they win over `mapping.yaml`.

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
