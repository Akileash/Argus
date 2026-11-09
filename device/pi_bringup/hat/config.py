"""All pin numbers and tunable thresholds for the on-hat stack.

Hardware (current build — DRV2605L, not L293D):
  LEFT DRV2605L on I2C1 (GPIO2/3)
  RIGHT DRV2605L + MPU-6050 on I2C3 (GPIO4/5) — requires dtoverlay=i2c3,pins_4_5
  HC-SR04 LEFT Trig23/Echo24, RIGHT Trig20/Echo21
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Paths / identity
# ---------------------------------------------------------------------------
HAT_DIR = Path(__file__).resolve().parent
ARGUS_DIR = HAT_DIR.parent
EVENTS_PATH = ARGUS_DIR / "events.jsonl"
CLIPS_DIR = ARGUS_DIR / "clips"
HAT_ID = "hat-01"

# ---------------------------------------------------------------------------
# Pins / I2C (BCM). Physical header pins in comments.
# ---------------------------------------------------------------------------

# I2C bus 1 — LEFT DRV2605L (0x5A)
#   SDA GPIO2 (pin 3), SCL GPIO3 (pin 5), 3V3 (pin 1), GND (pin 6)
#   (bring-up 2026-11-08: MPU is NOT on bus 1; SCD41 0x62 may also appear)
I2C_BUS_HAPTIC_LEFT = 1
MPU_ADDR = 0x68
DRV_ADDR = 0x5A

# I2C bus 3 — RIGHT DRV2605L (0x5A) + MPU-6050 (0x68) + VL6180X (0x29)
#   SDA GPIO4 (pin 7), SCL GPIO5 (pin 29), 3V3 (pin 17), GND (pin 9)
#   Verified: MPU WHO_AM_I=0x70 at 0x68 on bus 3 (not bus 1)
I2C_BUS_IMU = 3
I2C_BUS_HAPTIC_RIGHT = 3
I2C_BUS_TOF = 3
TOF_ADDR = 0x29

# Back-compat alias used by imu.py
I2C_BUS = I2C_BUS_IMU

# HC-SR04 LEFT
US_LEFT_TRIG = 23   # pin 16
US_LEFT_ECHO = 24   # pin 18  (via 1k/2k divider)

# HC-SR04 RIGHT
US_RIGHT_TRIG = 20  # pin 38
US_RIGHT_ECHO = 21  # pin 40  (via 1k/2k divider)

# ---------------------------------------------------------------------------
# Ultrasonic
# ---------------------------------------------------------------------------
US_SOUND_MPS = 343.0
US_MAX_RANGE_M = 3.0
US_MIN_RANGE_M = 0.05
US_ECHO_TIMEOUT_S = 0.024
# Chirp every 40 ms; dual mode alternates → ~12.5 Hz per sensor
US_PING_GAP_S = 0.040
US_MEDIAN_N = 1
US_SPEED_WINDOW = 2
US_MIN_SPEED_DT_S = 0.020
US_MIN_SAMPLES_TO_FIRE = 2
US_CLOSING_VOTES = 1
US_TRACK_BREAK_S = 0.20
US_MAX_PLAUSIBLE_MPS = 8.0
US_WARN_DISTANCE_M = 1.80
US_WARN_CLOSING_MPS = 0.40
US_NEAR_DISTANCE_M = 0.70
US_NEAR_CLOSING_MPS = 0.15
US_INSTANT_DROP_M = 0.12
US_COOLDOWN_S = 1.5
US_ENABLE_LEFT = True
US_ENABLE_RIGHT = True
# Matching-side buzz (left US → left DRV, right US → right DRV)
HAPTIC_WARN_BOTH = False
US_DEBUG = True

# ---------------------------------------------------------------------------
# IMU (MPU-6050)
# ---------------------------------------------------------------------------
IMU_SAMPLE_HZ = 200.0
IMU_BUFFER_S = 2.0
ACCEL_LSB_PER_G = 2048.0        # ±16 g
GYRO_LSB_PER_DPS = 32.8         # ±1000 dps
# Tuned from 2026-11-08 live misses:
#   Fall1 ~t=19s: |a| dipped to 0.14g then recovered ~1.05g (soft landing)
#   Fall2 ~t=39s: |a| dipped to 0.34g then recovered ~1.00g
# Old FREEFALL_G=0.25 missed Fall2; IMPACT_AFTER_FF_G=4.0 / FALL_MIN_PEAK_G=5.0
# rejected soft landings that never hit 4–5 g.
FREEFALL_G = 0.45              # catch 0.34 g weightlessness dips
FREEFALL_MIN_S = 0.050         # ~50 ms — brief unload still counts as fall
IMPACT_AFTER_FF_G = 1.6        # soft landing spike after freefall
IMPACT_AFTER_FF_WINDOW_S = 1.0 # impact must follow freefall within ~1 s
STILL_BAND_G = 0.20
STILL_HOLD_S = 0.300           # lie still this long after impact/landing
FALL_STILL_DEADLINE_S = 3.0    # no stillness by then → not a fall (kept moving)
FALL_MIN_PEAK_G = 1.0          # soft catch returns ~1 g; don't require 5 g spike
IMPACT_G = 3.0                 # hard hit w/o freefall: candidate only; needs stillness + tilt
# Soft path: freefall long enough then back near 1 g with no hard spike
SOFT_LANDING_AFTER_FF_S = 0.080
# Orientation check: angle between gravity before onset and while lying still.
# Jumps, nods, bumps while upright stay below this; falls onto a mat do not.
FALL_TILT_MIN_DEG = 30.0
FALL_TILT_EXEMPT_FF_S = 0.300  # >=300 ms true freefall (drop from height): skip tilt
FALL_REF_WINDOW_S = 1.0
# Hat knocked off by the fall (ToF raw not-near run) confirms WITHOUT tilt/stillness.
KNOCK_OFF_ENABLE = True
KNOCK_OFF_RUN_S = 0.5          # unbroken not-near run; > impact transient (~0.1-0.2 s),
                               # well inside the 3 s HELMET OFF dwell so the fall is logged first
KNOCK_OFF_PRE_S = 0.15         # run may start at most this long before fall onset
KNOCK_TOF_STALE_S = 0.3        # newest ToF poll older than this = ToF unavailable
# One fall = one event: after an event wait for cooldown AND this much quiet.
IMU_COOLDOWN_S = 3.0
REARM_QUIET_S = 1.0
REARM_BAND_G = 0.30

# Sensor-config guard (chip can silently fall back to ±2 g → readings 8x big)
MPU6500_FAMILY_IDS = (0x70, 0x71, 0x73)   # WHO_AM_I of MPU-6500/9250/9255
IMU_CONFIG_CHECK_S = 0.25      # read back config registers this often
IMU_CONFIG_LOSS_PURGE_S = 0.6  # discard this much recent data on loss (> check period)
IMU_SETTLE_AFTER_REINIT_S = 0.3
IMU_IMPLAUSIBLE_G = 3.0        # a window mostly above this is not a resting head
IMU_IMPLAUSIBLE_WINDOW_S = 1.0
IMU_IGNORE_AFTER_BUZZ_S = 0.250

# Wear gate: only emit fall if ToF said helmet ON beforehand.
# Lookback ends at freefall/hard-hit onset. Hat-off after landing
# does NOT suppress the fall. IMU does not invent hat_removed — ToF owns wear.
WEAR_GATE_ENABLE = True
WEAR_GATE_LOOKBACK_S = 3.0
WEAR_GATE_MIN_ON_FRACTION = 0.50
TOF_ENABLE = True
TOF_NEAR_MM_MIN = 50
TOF_NEAR_MM_MAX = 254
TOF_ON_DWELL_MS = 5000
TOF_OFF_DWELL_MS = 3000
TOF_POLL_GAP_S = 0.05

# ---------------------------------------------------------------------------
# Haptics (DRV2605L + ERM disc motors)
# ---------------------------------------------------------------------------
# ROM effect 14 = Strong Click 100%; 47 = Buzz 1 100% — pick a strong buzz
HAPTIC_EFFECT_ID = 47
HAPTIC_WARN_PULSES = 3
HAPTIC_WARN_ON_S = 0.200
HAPTIC_WARN_OFF_S = 0.080
# Soft-fail if RIGHT bus missing so LEFT still works during bring-up
HAPTIC_REQUIRE_RIGHT = False

# ---------------------------------------------------------------------------
# Pi Camera (libcamera / picamera2) — fall clip: PRE before + POST after
# ---------------------------------------------------------------------------
CAM_ENABLE = True
CAM_SIZE = (1280, 720)          # (width, height)
CAM_FPS = 30
CAM_BITRATE = 2_000_000         # bits/s
CAM_PRE_S = 20.0                # rolling buffer kept continuously
CAM_POST_S = 10.0               # keep recording this long after a fall
# IMU types that save a Pi Cam clip (and end --until-fall)
CAM_CLIP_TYPES = ("fall",)
