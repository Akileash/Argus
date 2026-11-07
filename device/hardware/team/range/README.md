# team/range: paste-your-script template

Status: **template only.** `create()` raises `NotImplementedError("team impl for range not pasted
yet - see README")`, so the supervisor treats this impl as unavailable and falls back (spec §5.4).

## Flow (spec §5.5)

1. Send Claude your tested range script with the hand-off template:

   ```
   Module: range
   Wiring: pins / I2C address / power used
   Libraries + versions: (pip freeze excerpt)
   What it outputs: example output lines (10-20 lines of real data)
   Units + rate: e.g. "distance in cm, ~10 readings/s"
   Known quirks: e.g. "sensor 3 reads 0 sometimes", "needs 2s warmup"
   ```

2. The script is copied here as `their_script.py`, **hardware logic unchanged**.
3. `adapter.py` converts it to the contract: mm (their script may print cm: multiply by 10); None = no echo; sensor_id R2 or R3 for the MVP (full set: R1,R2,R3,R4,TOP).
4. Run the contract tests with the hardware attached:
   `uv run pytest tests/contract --module range --impl team`
5. Select it in `config.yaml` (`impl: team` or `fallback: team`) and `argus-cli reload range`.

## Two adapter patterns (both sketched in `adapter.py`)

- **(a) import-and-call in a thread**: their code exposes a function; the adapter calls it in a loop.
- **(b) subprocess + stdout**: their script is a blocking `while True: print(...)`. The adapter runs
  it unchanged and parses each printed line (stamped on arrival with the shared clock).

Shared helpers: `hardware/team/_adapter_base.py` (`StreamAdapter`, `SubprocessAdapter`).

## Rules the contract enforces

Monotonic clock only, `read()` returns within 5 ms and drains everything since the last call, never
raise out of `read()`/`set()`, honest `health()`. See `argus/contracts.py`.
