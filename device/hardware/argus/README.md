# Argus hardware drivers (impl `argus`)

Each module lives at `hardware/argus/<module>/__init__.py` and exposes `create(ctx)`. Hardware libraries
(`smbus2`, `lgpio`, `picamera2`, `adafruit-*`) are imported lazily in `start()`; install with `uv sync --locked --extra pi`.
`start()` never raises: an init failure bumps `error_count`, sets `health().ok = False`, and the supervisor's
staleness check triggers the fallback impl. Each driver runs its own thread; `read()` only drains a queue.

All drivers talk to hardware through `_common.I2CBus` / `_common.Gpio` (or an injected device object), which is
what the unit tests fake.

## Options per module (`modules.<name>.options`)

| Module | Key (default) |
|---|---|
| `imu` | `i2c_bus` (1), `address` (0x68), `sample_rate_hz` (1000), `dlpf_cfg` (0), `drain_interval_ms` (20, clamped to <= 40), `read_chunk_samples` (20), `axis_map` ({x: "+x", y: "+y", z: "+z"}), `queue_max` (8192), `stale_ms` (500) |
| `range` | `pins` ({R1:[5,6], R2:[19,26], R3:[23,24], R4:[16,25], TOP:[20,21]} as [trig, echo]), `order` ([R2,R3], the MVP pair; list more ids to add sensors, e.g. [R1,R3,TOP,R2,R4] for the full crosstalk-minimising order), `slot_ms` (30), `trigger_us` (10), `echo_timeout_ms` (slot-3), `min_range_mm` (20), `max_range_mm` (4000), `temp_c` (null = 343 m/s), `gpiochip` (0), `stale_ms` (500) |
| `hat_on` | `timing_budget_us` (33000), `poll_interval_ms` (100 = 10 Hz, fixed rate), `max_range_mm` (1200; farther = out_of_range), `queue_max`, `stale_ms` (3000) |
| `env` | `poll_interval_s` (1.0), `temperature_offset_c` (null), `queue_max`, `stale_ms` (30000) |
| `haptics` | `pins` ({top: 12, back: 13}), `pwm_hz` (1000), `min_duty` (0.0), `max_duty` (1.0), `gpiochip` (0) |
| `camera` | `width` (1280), `height` (720), `fps` (30), `bitrate` (3000000), `sensor_size` ([1640,1232]), `ring_seconds` (40, must be >= 30; `ring_s` is accepted as an alias, the mock uses that name), `ffmpeg` ("ffmpeg"), `max_post_wait_s` (15), `stale_ms` (2000) |

## Per-driver notes and wiring assumptions

**imu (MPU-6050, I2C 0x68, bus 1).** AD0 tied to GND. Registers used: PWR_MGMT_1 0x6B, SIGNAL_PATH_RESET 0x68,
SMPLRT_DIV 0x19, CONFIG 0x1A, GYRO_CONFIG 0x1B, ACCEL_CONFIG 0x1C, FIFO_EN 0x23, USER_CTRL 0x6A, FIFO_COUNT 0x72,
FIFO_R_W 0x74. Config: +-16 g, +-1000 deg/s, DLPF_CFG 0 (260 Hz). NOTE: with the DLPF off/260 Hz the gyro output
rate is 8 kHz, so SMPLRT_DIV = 7 gives 1 kHz (not 0). FIFO holds accel+gyro (12 B/sample), drained every 20 ms in
<= 20-sample I2C bursts. FIFO count >= 1024 = overflow -> FIFO reset, `error_count++`, note. Timestamps: newest
sample = drain time, earlier ones back-dated by 1 ms (max error ~ one I2C transaction, a few hundred us).
Axis map: `axis_map: {x: "+y", y: "-x", z: "+z"}` means hat x = +sensor Y etc. Default identity assumes the GY-521
is mounted with sensor X forward, Y left, Z up (the silkscreen arrows). INT pin (GPIO4) is not used by this driver.
The I2C baud rate must be 400 kHz: `install.sh` adds `dtparam=i2c_arm_baudrate=400000` to `/boot/firmware/config.txt` (reboot
needed). `argus-cli test imu` fails if the real driver measures < 800 Hz or if the FIFO overflows during the test.

**range (HC-SR04, MVP = 2 rear sensors R2 + R3, spec §3.2a).** TRIG/ECHO pins per spec §2.3 (R1, R4, TOP are reserved). ECHO must go through the 1k/2k divider to 3.3 V. One sensor
fires at a time, 30 ms slot (60 ms sweep for the pair, ~16.7 Hz per sensor; 150 ms only if all 5 are enabled). Trigger = lgpio `tx_pulse` (10 us, timed in the lgpio library); echo
width = difference of lgpio alert timestamps (edge callbacks, no polling). Timeout, or a width outside 20..4000 mm,
yields `None`. mm = width_us * 0.343 / 2 (or with `temp_c`: 331.3 + 0.606*T m/s). `set_temperature()` allows runtime
compensation. Each sample's `t_ns` is the trigger time.

**hat_on (VL53L0X, 0x29).** Adafruit library, continuous mode. Raw >= 8190 or above `max_range_mm` -> `out_of_range`
with `range_mm=None`; library timeout/exception -> `error`. Mount >= 50 mm from the head (spec §2.5). Polled at a fixed
10 Hz (`poll_interval_ms` 100; the blocking read time is subtracted, so the rate does not drift to ~7 Hz).
The hat-on detector only accepts a baseline of <= `thresholds.hat_state.baseline_max_mm` (120 mm), see docs/CONNECT-THE-HAT.md.

**env (SCD41, 0x62).** Adafruit library, periodic measurement (5 s); the thread polls `data_ready` each second so
output is ~0.2 Hz. `temperature_offset_c` is applied while idle before starting.

**haptics (L293D).** Motor EN pins: top GPIO12, back GPIO13; **software** PWM by lgpio `tx_pwm` at 1 kHz (not the SoC PWM
block, so no `dtoverlay=pwm-2chan` is installed: it would put the pins in a PWM alt function and could block the claim; remove
it from `/boot/firmware/config.txt` if an older install added it). Timing jitter is irrelevant for a vibration motor; intensity 0..1 ->
duty 0..100 % (0 -> exactly 0; `min_duty`/`max_duty` allow a stall-compensating range). The L293D cannot report
motor current, so `health().ok` only means "GPIOs claimed and the last write succeeded", and the note says so.
`stop()` turns motors off.

**camera (Pi Cam V2.1 / IMX219).** picamera2, binned 1640x1232 sensor mode -> 1280x720 @ 30 fps, `H264Encoder`
(`repeat=True` = SPS/PPS inline on every keyframe, keyframe every 1 s). A small picamera2 `Output` subclass (it accepts
the varying `outputframe(frame, keyframe, timestamp, ...)` signature and ignores `timestamp`) feeds our own ring:
a time-bounded deque of `(t_ns, is_keyframe, bytes)` sized by `ring_seconds` (40 s default; the spec needs pre 20 s +
post 10 s). `t_ns` is `clock.now_ns()` (CLOCK_MONOTONIC) when the frame arrives from the encoder, i.e. the exposure time
plus the encoder latency (tens of ms). picamera2's own `timestamp` is not used because it is rebased to 0 at encoder start.
`save_clip(t_start, t_end, path)` waits (up to `max_post_wait_s`, only that call blocks) for frames up to `t_end`, copies
the ring without draining it (so overlapping or back-to-back events each get a full clip), starts at the last keyframe at
or before `t_start` (up to 1 s early), writes raw H.264 to `<path>.raw.h264` and runs
`ffmpeg -framerate <fps> -f h264 -i raw -c copy -movflags +faststart out.mp4` (no re-encode, no `-ss`: seeking inside a raw
elementary stream produced an empty MP4 on ffmpeg 9). `ClipInfo.t_start_ns`/`t_end_ns` are the first and last frame
actually written; the event manager derives `window.pre_s`/`post_s` from them. The window is clamped to what is in the ring.
`ffmpeg` must be installed (`sudo apt install ffmpeg`). Rear USB camera is not implemented (out of scope).

**Python / numpy.** `pyproject.toml` pins `numpy<2`. The install venv uses system site-packages, and Bookworm's apt
`python3-av` / `python3-simplejpeg` (pulled in by `python3-picamera2`) are built against numpy 1.x, so a numpy 2 wheel in the
venv would shadow the system one and could break `import picamera2`. This is inferred from how Bookworm packages those
libraries; it has not been reproduced on a Pi.

## Bench bring-up checklist (on the Pi)

Run every `argus-cli` command below with `--config /etc/argus/config.yaml`, the file the service reads. Without it the CLI reads the repo's mock config and the tests run against mocks. Stop the service first (`sudo systemctl stop argus`) because the sensors are exclusive.

1. `sudo raspi-config nonint do_i2c 0`; `uv sync --locked --extra pi`; `sudo apt install ffmpeg i2c-tools`.
2. `i2cdetect -y 1` -> expect `0x29` (VL53L0X), `0x62` (SCD41), `0x68` (MPU-6050, not 0x69).
3. `argus-cli test imu --config /etc/argus/config.yaml`: accel z ~ +1 g when flat (hat frame z up), rate >= 800 Hz for this driver (expect ~1000; the test fails below that), no FIFO overflows
   (`error_count` must not grow; the test fails if it does). Needs the 400 kHz I2C baud rate. Tilt/tap: check axis signs; set `axis_map` if x/y/z are wrong. Shake hard: no FIFO overflow notes.
4. `argus-cli test range --config /etc/argus/config.yaml`: for each sensor hold a flat target at 0.5 / 1.0 / 2.0 m; error should be within +-1 cm;
   R2 and R3 both report every ~60 ms; unplugged echo -> `None`. If jitter > +-1 cm,
   switch `range` to the `team` impl or the Arduino fallback (spec §2.4). Check ECHO divider voltage (<= 3.3 V) first.
5. `argus-cli test hat_on --config /etc/argus/config.yaml`: hat on head -> `ok` ~50-80 mm; off head -> `out_of_range`.
6. `argus-cli test env --config /etc/argus/config.yaml`: first sample after ~5 s, CO2 ~ 400-1000 ppm.
7. `argus-cli buzz top|back|both --config /etc/argus/config.yaml`: motors spin at the right pins; check with a scope/LED that GPIO12/13 show 1 kHz PWM;
   if the motors stall at low intensity raise `min_duty`.
8. `argus-cli test camera --config /etc/argus/config.yaml`: 720p30 recording, then `save_clip` of the last 10 s: `ffprobe clip.mp4` shows h264
   1280x720 ~30 fps and a duration ~ requested. Save two clips back to back
   (e.g. `argus-cli trigger fall --config /etc/argus/config.yaml` twice, 15 s apart) and check both are full length and play from the start. Confirm the sensor mode in picamera2
   (`cam.camera_configuration()['sensor']`) is 1640x1232.
9. Power: run all modules together for 10 min, watch `vcgencmd get_throttled` (expect 0x0) with motors firing.
