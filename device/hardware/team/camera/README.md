# team/camera: plug in your script

Status: **not configured yet.** Until `mapping.yaml` has a `save_command`, `create()` raises
`NotImplementedError("team impl for camera not pasted yet")` and the supervisor falls back (spec section 5.4).

The camera adapter is deliberately minimal (spec sections 3.1, 5.5). Your script keeps its own rolling buffer
(30 s or more) and can save the last N seconds to an MP4 on request. The adapter runs it as a subprocess.

## Steps

1. Copy your capture and clip scripts into `vendor/`. Do not change them.
2. `cd device && uv run argus-cli team init camera`, then fill in `mapping.yaml`:

   ```yaml
   command: [python, vendor/capture.py]       # optional: long-running capture, started and stopped with the module
   save_command: [python, vendor/save_clip.py, --seconds, "{seconds}", --out, "{path}"]
   save_timeout_s: 30
   ```

   Placeholders in `save_command`: `{path}` the file to write, `{seconds}` the clip length,
   `{end_ago_s}` how long ago the clip should end, `{start_ns}` and `{end_ns}` the window on the shared clock.
3. On the Pi: `uv run pytest tests/contract --module camera --impl team`.
4. Turn it on in `device/config.local.yaml` (gitignored): `modules: {camera: {impl: team, fallback: mock}}`,
   then `uv run argus-cli reload camera`. The camera owns the hardware, so the old impl is stopped first.
5. Send the integrator the hand-off template below, then open a pull request that touches only
   `device/hardware/team/camera/`.

```
Module: camera
Wiring: pins / I2C address / power used
Libraries + versions: (pip freeze excerpt)
What it outputs: 10-20 lines of real output, pasted as is
Units + rate: e.g. "distance in cm, ~10 readings/s"
Known quirks: e.g. "sensor 3 reads 0 sometimes", "needs 2s warmup"
```

## What the module must deliver

`save_clip(t_start_ns, t_end_ns, path) -> ClipInfo` writing a real MP4. Frames must be stamped with the shared
monotonic clock (`time.monotonic_ns()`), not the camera's own clock. If `save_command` fails or writes
nothing, `save_clip` raises and the event keeps its sensor data without video. If your clip tool needs
per-frame timestamps or a segment ring, write a class in `adapter.py` instead (the mock camera in
`hardware/mock/camera` is a complete working reference).
