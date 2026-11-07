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
