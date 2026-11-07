# team/hat_on: paste-your-script template

Status: **template only.** `create()` raises `NotImplementedError("team impl for hat_on not pasted
yet - see README")`, so the supervisor treats this impl as unavailable and falls back (spec §5.4).

## Flow (spec §5.5)

1. Send Claude your tested hat_on script with the hand-off template:

   ```
   Module: hat_on
   Wiring: pins / I2C address / power used
   Libraries + versions: (pip freeze excerpt)
   What it outputs: example output lines (10-20 lines of real data)
   Units + rate: e.g. "distance in cm, ~10 readings/s"
   Known quirks: e.g. "sensor 3 reads 0 sometimes", "needs 2s warmup"
   ```

2. The script is copied here as `their_script.py`, **hardware logic unchanged**.
3. `adapter.py` converts it to the contract: mm; status ok | out_of_range | error (VL53L0X returns ~8190 mm when nothing is in range).
4. Run the contract tests with the hardware attached:
   `uv run pytest tests/contract --module hat_on --impl team`
5. Select it in `config.yaml` (`impl: team` or `fallback: team`) and `argus-cli reload hat_on`.

## Two adapter patterns (both sketched in `adapter.py`)

- **(a) import-and-call in a thread**: their code exposes a function; the adapter calls it in a loop.
- **(b) subprocess + stdout**: their script is a blocking `while True: print(...)`. The adapter runs
  it unchanged and parses each printed line (stamped on arrival with the shared clock).

Shared helpers: `hardware/team/_adapter_base.py` (`StreamAdapter`, `SubprocessAdapter`).

## Rules the contract enforces

Monotonic clock only, `read()` returns within 5 ms and drains everything since the last call, never
raise out of `read()`/`set()`, honest `health()`. See `argus/contracts.py`.
