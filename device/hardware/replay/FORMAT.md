# Argus sensor recording format (`.jsonl`)

One JSON object per line. Written by `argus-cli record` (later task) and by
`hardware.replay.recording.write_recording`; read by the `replay` impls (spec §5.3, §5.6).

```jsonl
{"meta": {"source": "mock:board_swing_rear_left", "created": "2026-11-07T12:00:00Z"}}
{"stream": "imu",   "t_ns": 1000000000, "ax": 0.01, "ay": -0.00, "az": 1.01, "gx": 0.4, "gy": -0.3, "gz": 0.2}
{"stream": "range", "t_ns": 1000000000, "sensor_id": "R1", "range_mm": 2390.5}
{"stream": "range", "t_ns": 1030000000, "sensor_id": "R2", "range_mm": null}
{"stream": "tof",   "t_ns": 1000000000, "range_mm": 62.1, "status": "ok"}
{"stream": "env",   "t_ns": 1000000000, "co2_ppm": 648.1, "temp_c": 22.4, "rh_pct": 40.9}
```

Rules

- `meta` line is optional and, if present, must be first. Readers skip it.
- `stream` is one of `imu | range | tof | env`. Field names and units are exactly the sample
  dataclasses in `argus/contracts.py`: accel in g, gyro in deg/s, ranges in mm (`null` = no echo),
  `status` is `ok | out_of_range | error`, `sensor_id` is `R1..R4 | TOP`.
- `t_ns` is an integer nanosecond timestamp from one monotonic clock for all streams of a file.
  Its absolute value is irrelevant: replay uses `t_ns - min(t_ns)` as the time axis.
- Lines should be sorted by `t_ns` (readers sort anyway). Blank lines are ignored.
- Haptics and camera are not recorded. The replay camera/haptics impls are the mocks.

Replay options (`modules.<name>.options`): `file` (path, required), `speed` (default 1.0; 10 = ten
times faster, timestamps are compressed accordingly), `loop` (default false). All replay modules of
one file share a time origin so streams stay in sync.

Recordings go in `device/recordings/` (gitignored except `recordings/curated/`, the permanent
regression set).

## Labels sidecar

`argus-cli record --label <name>` also writes `<recording>.labels.json` next to the recording (same stem,
`20261107T120000Z_walk.jsonl` -> `20261107T120000Z_walk.labels.json`; the tuning tools also accept
`<recording>.jsonl.labels.json`). It tells `eval` / `sweep` what the detectors should find:

```json
{"label": "walk_backward_wall",
 "expected": [{"type": "struck_by_near_miss", "t_s": 12.4, "sensor": "R2"},
              {"type": "hat_removed", "t_window_s": [5.0, 8.6]}],
 "notes": "free text"}
```

- `expected` is a list (empty = the recording must produce NO events, e.g. a false-positive check).
- `type`: `struck_by_near_miss | struck_by_contact | head_impact | fall | hat_knocked_off | hat_removed`.
- `t_s` (optional): seconds from the first sample of the recording (`t_ns - min(t_ns)`, the same axis replay
  uses). `sensor` (optional, one of `R1..R4|TOP`) or `sensors` (list of acceptable ones); `t_window_s`
  (optional `[lo, hi]`) replaces the `t_s +- tolerance` match window.
- `argus-cli record --expect type[:t_s[:sensor]]` (repeatable) fills `expected`; `--notes` fills `notes`.
