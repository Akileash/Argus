#!/usr/bin/env python3
"""
Smart hard-hat IMU fall / impact detector (MPU-6050 only).

Modes:
  record          — capture labeled CSV for offline review
  calibrate-worn  — learn worn vs table gyro noise floor
  monitor         — live detection → events.jsonl
  selftest        — synthetic streams, no hardware

All thresholds / score weights are named constants below for easy tuning.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import struct
import sys
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Deque, Iterable, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Paths (everything stays under ~/argus)
# ---------------------------------------------------------------------------
ARGUS_DIR = Path(__file__).resolve().parent
RECORDINGS_DIR = ARGUS_DIR / "recordings"
CALIB_PATH = ARGUS_DIR / "calib.json"
EVENTS_PATH = ARGUS_DIR / "events.jsonl"
HAT_ID = "hat-01"

# ---------------------------------------------------------------------------
# MPU-6050 register map / scaling (matches mpu6050_test.py)
# ---------------------------------------------------------------------------
I2C_BUS = 1
ADDR = 0x68
PWR_MGMT_1, SMPLRT_DIV, CONFIG = 0x6B, 0x19, 0x1A
GYRO_CONFIG, ACCEL_CONFIG, ACCEL_XOUT_H = 0x1B, 0x1C, 0x3B
ACCEL_LSB_PER_G = 2048.0       # +/-16 g
GYRO_LSB_PER_DPS = 65.5        # +/-500 deg/s

# ---------------------------------------------------------------------------
# Sampling / buffers
# ---------------------------------------------------------------------------
SAMPLE_HZ = 200.0
SAMPLE_DT = 1.0 / SAMPLE_HZ
BUFFER_SECONDS = 6.0           # long enough for imu_trace (-2 .. +3 s)
I2C_RETRIES = 5
I2C_RETRY_SLEEP = 0.002

# ---------------------------------------------------------------------------
# Trigger (Tier 1) — raised to ignore light taps / handling
# ---------------------------------------------------------------------------
IMPACT_G = 5.0                 # accel magnitude trigger (was 3.0; kills finger taps)
GYRO_TRIGGER_DPS = 350.0       # high rotation trigger (with accel)
GYRO_TRIGGER_ACCEL_G = 3.0     # accel floor when using gyro trigger
FREEFALL_G = 0.35              # free-fall magnitude threshold
FREEFALL_LOOKBACK_S = 0.7      # look for free-fall in this window before trigger
GRAVITY_PRE_S = 0.5            # gravity vector taken this far before trigger

# ---------------------------------------------------------------------------
# Evaluation window
# ---------------------------------------------------------------------------
RINGING_IGNORE_S = 0.30
EVAL_WINDOW_S = 3.0
STILL_WINDOW_S = 1.5           # final portion used for stillness
STILL_GYRO_MEAN_DPS = 10.0
STILL_ACCEL_STD_G = 0.15
EVENT_COOLDOWN_S = 8.0         # longer so one drop ≠ multiple events

# ---------------------------------------------------------------------------
# What actually gets written to events.jsonl / printed
# Near-misses are still classified internally, but not logged.
# Soft set-downs are not logged. hat_removed is logged so we don't
# mis-label a knock-off as a fall.
# ---------------------------------------------------------------------------
LOG_TYPES = frozenset({"fall", "impact", "hat_removed"})
HARD_IMPACT_LOG_G = 5.5        # impacts below this are not logged
FALL_MIN_PEAK_G = 5.0          # fall without meaningful freefall needs this peak
FALL_MIN_FREEFALL_MS = 80.0    # OR this much freefall to log a fall
SUPPRESS_SET_DOWN = True       # do not log hat_possibly_set_down events

# ---------------------------------------------------------------------------
# Classification score weights (must sum ~1.0; tune freely)
# ---------------------------------------------------------------------------
W_IMPACT = 0.28
W_GYRO = 0.18
W_TILT = 0.18
W_STILL = 0.22
W_FREEFALL = 0.14

# Feature normalization anchors
IMPACT_SCORE_SPAN_G = 5.0      # (peak - IMPACT_G) / span → 0..1
GYRO_SCORE_SPAN_DPS = 400.0
TILT_SCORE_SPAN_DEG = 90.0
FREEFALL_SCORE_SPAN_MS = 250.0

# Type decision thresholds
FALL_SCORE_MIN = 0.48
FALL_PEAK_G = 5.0              # tumbling-style fall without free-fall
FALL_PEAK_GYRO_DPS = 280.0
HAT_REMOVED_TILT_DEG = 60.0
SET_DOWN_TILT_MAX_DEG = 35.0   # small tilt + still + not worn → set-down flag
SET_DOWN_MAX_PEAK_G = 5.5
SET_DOWN_MAX_FREEFALL_MS = 80.0

# ---------------------------------------------------------------------------
# Worn confidence (IMU-only; NEVER suppresses events)
# ---------------------------------------------------------------------------
DEFAULT_WORN_GYRO_STD = 4.0    # dps; used if calib.json missing
WORN_HIGH_FACTOR = 1.5         # gyro_std > thr * factor → high
WORN_MED_FACTOR = 0.8          # gyro_std > thr * factor → medium else low
NOT_WORN_HOLD_S = 2.0          # low motion this long → not_worn (~3s intent; 1s window lags)
WORN_BURST_GYRO_DPS = 25.0     # one clear motion burst → worn again
WORN_WINDOW_S = 1.0

# ---------------------------------------------------------------------------
# Trace export
# ---------------------------------------------------------------------------
TRACE_HZ = 50.0
TRACE_PRE_S = 2.0
TRACE_POST_S = 3.0

# ---------------------------------------------------------------------------
# Calibration defaults / helpers
# ---------------------------------------------------------------------------
CALIB_PHASE_S = 20.0
CALIB_WINDOW_S = 1.0
GYRO_CAL_S = 1.0
GYRO_CAL_MOVE_WARN_DPS = 15.0  # mean |gyro| during bias cal


# =============================================================================
# Data types
# =============================================================================

@dataclass
class Sample:
    """One IMU sample in physical units."""
    t: float
    ax: float
    ay: float
    az: float
    gx: float
    gy: float
    gz: float

    @property
    def accel_mag(self) -> float:
        return math.sqrt(self.ax * self.ax + self.ay * self.ay + self.az * self.az)

    @property
    def gyro_mag(self) -> float:
        return math.sqrt(self.gx * self.gx + self.gy * self.gy + self.gz * self.gz)

    @property
    def accel_vec(self) -> Tuple[float, float, float]:
        return (self.ax, self.ay, self.az)


@dataclass
class PendingEvent:
    """State for an in-progress Tier-1 evaluation."""
    trigger_t: float
    peak_g: float
    peak_gyro: float
    freefall_ms: float
    gravity_pre: Tuple[float, float, float]
    worn_before: bool
    worn_conf_before: str
    eval_end_t: float
    ringing_end_t: float


@dataclass
class WornState:
    is_worn: bool = True
    confidence: str = "medium"
    _low_since: Optional[float] = None


# =============================================================================
# Math helpers
# =============================================================================

def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _std(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    return statistics.pstdev(values)


def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _vec_norm(v: Tuple[float, float, float]) -> float:
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) or 1e-9


def tilt_degrees(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> float:
    """Angle between two gravity vectors in degrees."""
    na, nb = _vec_norm(a), _vec_norm(b)
    dot = (a[0] * b[0] + a[1] * b[1] + a[2] * b[2]) / (na * nb)
    return math.degrees(math.acos(_clamp(dot, -1.0, 1.0)))


def iso_local_now() -> str:
    """ISO 8601 local time with offset."""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


# =============================================================================
# Calibration I/O
# =============================================================================

def load_calib() -> dict:
    """Load calib.json or return sane defaults."""
    defaults = {
        "worn_gyro_std_median": 12.0,
        "table_gyro_std_median": 1.5,
        "worn_threshold_gyro_std": DEFAULT_WORN_GYRO_STD,
    }
    if not CALIB_PATH.exists():
        return defaults
    try:
        with CALIB_PATH.open() as f:
            data = json.load(f)
        out = dict(defaults)
        out.update(data)
        return out
    except (OSError, json.JSONDecodeError) as e:
        print(f"WARNING: could not read {CALIB_PATH}: {e}; using defaults", file=sys.stderr)
        return defaults


def save_calib(data: dict) -> None:
    with CALIB_PATH.open("w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


# =============================================================================
# Hardware layer
# =============================================================================

class MPU6050:
    """Thin smbus2 wrapper with retrying burst reads."""

    def __init__(self, bus_id: int = I2C_BUS, addr: int = ADDR):
        from smbus2 import SMBus  # local import keeps selftest import-light
        self.addr = addr
        self.bus = SMBus(bus_id)
        self.bias = (0.0, 0.0, 0.0)
        self.i2c_errors = 0
        self._setup()

    def _setup(self) -> None:
        self.bus.write_byte_data(self.addr, PWR_MGMT_1, 0x00)  # wake
        time.sleep(0.1)
        self.bus.write_byte_data(self.addr, CONFIG, 0x02)       # DLPF ~94 Hz
        self.bus.write_byte_data(self.addr, SMPLRT_DIV, 0x04)   # ~200 Hz
        self.bus.write_byte_data(self.addr, GYRO_CONFIG, 0x08)  # +/-500 dps
        self.bus.write_byte_data(self.addr, ACCEL_CONFIG, 0x18) # +/-16 g

    def _read_raw(self) -> Tuple[int, int, int, int, int, int, int]:
        last: Optional[OSError] = None
        for attempt in range(I2C_RETRIES):
            try:
                d = self.bus.read_i2c_block_data(self.addr, ACCEL_XOUT_H, 14)
                return struct.unpack(">hhhhhhh", bytes(d))
            except OSError as e:
                last = e
                self.i2c_errors += 1
                time.sleep(I2C_RETRY_SLEEP)
        assert last is not None
        raise last

    def calibrate_gyro(self, duration_s: float = GYRO_CAL_S) -> None:
        """Estimate gyro bias; warn if the hat moved."""
        n = max(1, int(duration_s * SAMPLE_HZ))
        sx = sy = sz = 0.0
        mags: List[float] = []
        for _ in range(n):
            _, _, _, _, gx, gy, gz = self._read_raw()
            sx += gx
            sy += gy
            sz += gz
            mags.append(math.sqrt(gx * gx + gy * gy + gz * gz) / GYRO_LSB_PER_DPS)
            time.sleep(SAMPLE_DT)
        self.bias = (sx / n, sy / n, sz / n)
        if _mean(mags) > GYRO_CAL_MOVE_WARN_DPS:
            print(
                f"WARNING: motion during gyro calibration "
                f"(mean |gyro|={_mean(mags):.1f} dps). Keep still and re-run if needed."
            )

    def read_sample(self, t: float) -> Optional[Sample]:
        """Return a Sample or None if I2C failed after retries."""
        try:
            ax, ay, az, _temp, gx, gy, gz = self._read_raw()
        except OSError as e:
            print(f"I2C error (counted, continuing): {e}", file=sys.stderr)
            return None
        bx, by, bz = self.bias
        return Sample(
            t=t,
            ax=ax / ACCEL_LSB_PER_G,
            ay=ay / ACCEL_LSB_PER_G,
            az=az / ACCEL_LSB_PER_G,
            gx=(gx - bx) / GYRO_LSB_PER_DPS,
            gy=(gy - by) / GYRO_LSB_PER_DPS,
            gz=(gz - bz) / GYRO_LSB_PER_DPS,
        )

    def close(self) -> None:
        try:
            self.bus.close()
        except OSError:
            pass


def sample_loop(duration_s: Optional[float], callback) -> None:
    """
    Drift-free ~200 Hz loop. callback(sample) is invoked for each good sample.
    Stops after duration_s (None = forever) or KeyboardInterrupt.
    """
    imu = MPU6050()
    try:
        print("Calibrating gyro (~1 s) — keep the hat still...")
        imu.calibrate_gyro()
        print("Sampling.")
        t0 = time.perf_counter()
        next_tick = t0
        while True:
            now = time.perf_counter()
            if duration_s is not None and (now - t0) >= duration_s:
                break
            delay = next_tick - now
            if delay > 0:
                time.sleep(delay)
            tick = time.perf_counter()
            # If we fell far behind, resync rather than spinning catch-up forever
            if tick - next_tick > SAMPLE_DT * 4:
                next_tick = tick
            sample = imu.read_sample(tick - t0)
            next_tick += SAMPLE_DT
            if sample is not None:
                callback(sample)
    finally:
        imu.close()


# =============================================================================
# Worn tracker (labels only — never drops events)
# =============================================================================

class WornTracker:
    def __init__(self, worn_threshold: float):
        self.threshold = worn_threshold
        self.window: Deque[Sample] = deque()
        self.state = WornState(is_worn=True, confidence="medium")

    def update(self, s: Sample) -> WornState:
        self.window.append(s)
        cutoff = s.t - WORN_WINDOW_S
        while self.window and self.window[0].t < cutoff:
            self.window.popleft()

        gyros = [x.gyro_mag for x in self.window]
        gyro_std = _std(gyros)
        conf = self._confidence(gyro_std)
        self.state.confidence = conf

        # Hysteresis on worn / not_worn
        low_motion = gyro_std < self.threshold * WORN_MED_FACTOR
        if self.state.is_worn:
            if low_motion:
                if self.state._low_since is None:
                    self.state._low_since = s.t
                elif s.t - self.state._low_since >= NOT_WORN_HOLD_S:
                    self.state.is_worn = False
                    self.state._low_since = None
            else:
                self.state._low_since = None
        else:
            # one clear motion burst returns to worn
            if s.gyro_mag >= WORN_BURST_GYRO_DPS or gyro_std > self.threshold * WORN_HIGH_FACTOR:
                self.state.is_worn = True
                self.state._low_since = None
        return self.state

    def _confidence(self, gyro_std: float) -> str:
        if gyro_std >= self.threshold * WORN_HIGH_FACTOR:
            return "high"
        if gyro_std >= self.threshold * WORN_MED_FACTOR:
            return "medium"
        return "low"

    def snapshot(self) -> Tuple[bool, str]:
        return self.state.is_worn, self.state.confidence


def should_log_event(event: dict) -> bool:
    """
    Gate what reaches events.jsonl / the dashboard.

    Near-falls are classified but not logged (handling / soft bumps).
    Soft set-downs are not logged. Only confident falls, hard impacts,
    and hat_removed (so knock-offs aren't mistaken for falls).
    """
    etype = event.get("type")
    sensor = event.get("sensor") or {}
    peak = float(sensor.get("peak_accel_g") or 0.0)
    freefall_ms = float(sensor.get("freefall_ms") or 0.0)

    if etype not in LOG_TYPES:
        return False
    if SUPPRESS_SET_DOWN and event.get("hat_possibly_set_down"):
        return False
    if etype == "impact" and peak < HARD_IMPACT_LOG_G:
        return False
    if etype == "fall":
        if peak < FALL_MIN_PEAK_G and freefall_ms < FALL_MIN_FREEFALL_MS:
            return False
    if etype == "hat_removed" and peak < IMPACT_G:
        return False
    return True


# =============================================================================
# Detector
# =============================================================================

class FallDetector:
    """
    Two-tier fall / impact detector. Feed samples one at a time via push().
    Same code path for live monitor and selftest.
    """

    def __init__(self, worn_threshold: Optional[float] = None, on_event=None):
        calib = load_calib()
        thr = worn_threshold if worn_threshold is not None else float(
            calib.get("worn_threshold_gyro_std", DEFAULT_WORN_GYRO_STD)
        )
        self.worn = WornTracker(thr)
        self.buffer: Deque[Sample] = deque()
        self.pending: Optional[PendingEvent] = None
        self.cooldown_until = -1.0
        self.ignore_until_t = -1.0
        self.on_event = on_event or default_on_event
        self.last_status = {
            "state": "idle",
            "accel_mag": 0.0,
            "worn": True,
            "worn_confidence": "medium",
        }

    def ignore_until(self, timestamp: float) -> None:
        """Mask motor / haptic vibration until `timestamp` (perf/sample time)."""
        self.ignore_until_t = max(self.ignore_until_t, timestamp)

    def push(self, s: Sample) -> Optional[dict]:
        """Ingest one sample. Returns an event dict if one completes this tick."""
        self._trim(s)
        self.buffer.append(s)
        worn_state = self.worn.update(s)

        self.last_status = {
            "state": "evaluating" if self.pending else "idle",
            "accel_mag": s.accel_mag,
            "worn": worn_state.is_worn,
            "worn_confidence": worn_state.confidence,
        }

        if s.t < self.ignore_until_t:
            return None

        event: Optional[dict] = None
        if self.pending is not None:
            event = self._maybe_finish(s)
        elif s.t >= self.cooldown_until:
            self._maybe_trigger(s)

        return event

    # --- internals ---------------------------------------------------------

    def _trim(self, s: Sample) -> None:
        cutoff = s.t - BUFFER_SECONDS
        while self.buffer and self.buffer[0].t < cutoff:
            self.buffer.popleft()

    def _samples_in(self, t0: float, t1: float) -> List[Sample]:
        return [x for x in self.buffer if t0 <= x.t <= t1]

    def _maybe_trigger(self, s: Sample) -> None:
        am, gm = s.accel_mag, s.gyro_mag
        hit = am >= IMPACT_G or (gm >= GYRO_TRIGGER_DPS and am >= GYRO_TRIGGER_ACCEL_G)
        if not hit:
            return

        # Free-fall duration in the lookback window
        look0 = s.t - FREEFALL_LOOKBACK_S
        freefall_s = 0.0
        prev_t = None
        for x in self._samples_in(look0, s.t):
            if x.accel_mag < FREEFALL_G:
                if prev_t is not None:
                    freefall_s += x.t - prev_t
                else:
                    freefall_s += SAMPLE_DT
            prev_t = x.t

        # Gravity ~0.5 s before trigger (average a short neighborhood)
        g_t = s.t - GRAVITY_PRE_S
        grav_samples = self._samples_in(g_t - 0.05, g_t + 0.05)
        if not grav_samples:
            grav_samples = self._samples_in(s.t - GRAVITY_PRE_S - 0.2, s.t - GRAVITY_PRE_S + 0.2)
        if grav_samples:
            gravity_pre = (
                _mean([x.ax for x in grav_samples]),
                _mean([x.ay for x in grav_samples]),
                _mean([x.az for x in grav_samples]),
            )
        else:
            gravity_pre = s.accel_vec

        worn_before, worn_conf = self.worn.snapshot()
        # Peak in a tiny window around the trigger sample
        near = self._samples_in(s.t - 0.05, s.t + 0.05) or [s]
        peak_g = max(x.accel_mag for x in near)
        peak_gyro = max(x.gyro_mag for x in near)

        self.pending = PendingEvent(
            trigger_t=s.t,
            peak_g=peak_g,
            peak_gyro=peak_gyro,
            freefall_ms=freefall_s * 1000.0,
            gravity_pre=gravity_pre,
            worn_before=worn_before,
            worn_conf_before=worn_conf,
            ringing_end_t=s.t + RINGING_IGNORE_S,
            eval_end_t=s.t + RINGING_IGNORE_S + EVAL_WINDOW_S,
        )
        self.last_status["state"] = "evaluating"

    def _maybe_finish(self, s: Sample) -> Optional[dict]:
        assert self.pending is not None
        p = self.pending

        # Track peaks through ringing + eval
        if s.accel_mag > p.peak_g:
            p.peak_g = s.accel_mag
        if s.gyro_mag > p.peak_gyro:
            p.peak_gyro = s.gyro_mag

        if s.t < p.eval_end_t:
            return None

        # Evaluation features from the final stillness window
        still_t0 = p.eval_end_t - STILL_WINDOW_S
        still_samples = self._samples_in(still_t0, p.eval_end_t)
        if len(still_samples) < 5:
            still_samples = self._samples_in(p.ringing_end_t, p.eval_end_t)

        gyro_mean = _mean([x.gyro_mag for x in still_samples])
        accel_std = _std([x.accel_mag for x in still_samples])
        still_after = (
            gyro_mean < STILL_GYRO_MEAN_DPS and accel_std < STILL_ACCEL_STD_G
        )

        if still_samples:
            gravity_post = (
                _mean([x.ax for x in still_samples]),
                _mean([x.ay for x in still_samples]),
                _mean([x.az for x in still_samples]),
            )
        else:
            gravity_post = s.accel_vec

        tilt = tilt_degrees(p.gravity_pre, gravity_post)
        worn_after, worn_conf_after = self.worn.snapshot()
        # Hysteresis can lag the eval window; stillness with a dead gyro
        # implies the hat is no longer being worn for labeling purposes.
        if still_after and gyro_mean < STILL_GYRO_MEAN_DPS * 0.5:
            worn_after = False

        event = self._classify(p, still_after, tilt, worn_after, worn_conf_after, gyro_mean)
        self.pending = None
        self.cooldown_until = s.t + EVENT_COOLDOWN_S
        self.last_status["state"] = "cooldown"

        if not should_log_event(event):
            peak = event.get("sensor", {}).get("peak_accel_g", 0)
            print(
                f"(suppressed {event.get('type')} peak={peak}g — "
                f"not confident enough to log)"
            )
            return None

        self.on_event(event)
        return event

    def _classify(
        self,
        p: PendingEvent,
        still_after: bool,
        tilt: float,
        worn_after: bool,
        worn_conf_after: str,
        gyro_mean: float = 0.0,
    ) -> dict:
        impact_n = _clamp((p.peak_g - IMPACT_G) / IMPACT_SCORE_SPAN_G, 0.0, 1.0)
        gyro_n = _clamp(p.peak_gyro / GYRO_SCORE_SPAN_DPS, 0.0, 1.0)
        tilt_n = _clamp(tilt / TILT_SCORE_SPAN_DEG, 0.0, 1.0)
        still_n = 1.0 if still_after else 0.0
        ff_n = _clamp(p.freefall_ms / FREEFALL_SCORE_SPAN_MS, 0.0, 1.0)

        score = (
            W_IMPACT * impact_n
            + W_GYRO * gyro_n
            + W_TILT * tilt_n
            + W_STILL * still_n
            + W_FREEFALL * ff_n
        )

        worn_changed = p.worn_before and (not worn_after)
        hat_set_down = False
        etype: str

        # Weighted / rule-assisted type selection (free-fall is a bonus, not required)
        if still_after and tilt >= HAT_REMOVED_TILT_DEG and (worn_changed or not worn_after):
            etype = "hat_removed"
        elif (
            still_after
            and (worn_changed or not worn_after)
            and tilt < SET_DOWN_TILT_MAX_DEG
            and p.peak_g < SET_DOWN_MAX_PEAK_G
            and p.freefall_ms < SET_DOWN_MAX_FREEFALL_MS
            and p.peak_gyro < FALL_PEAK_GYRO_DPS
        ):
            # Impact then perfectly still + worn→not_worn, small tilt
            etype = "impact"
            hat_set_down = True
        elif still_after and (
            p.freefall_ms >= 100
            or (p.peak_g >= FALL_PEAK_G and p.peak_gyro >= FALL_PEAK_GYRO_DPS)
            or (p.peak_g >= 6.0 and score >= FALL_SCORE_MIN)
            or (score >= FALL_SCORE_MIN + 0.15 and p.freefall_ms >= 80)
        ):
            etype = "fall"
        elif not still_after:
            etype = "near_fall"
        else:
            etype = "impact"
            if (worn_changed or not worn_after) and tilt < HAT_REMOVED_TILT_DEG:
                hat_set_down = True

        # Priority: worn state may only rank, never drop
        if etype == "fall":
            priority = "high"
        elif etype == "hat_removed":
            priority = "medium"
        elif hat_set_down:
            priority = "low"
        elif etype == "impact":
            priority = "medium" if p.worn_before else "low"
        else:  # near_fall
            priority = "low"

        if score >= 0.65:
            confidence = "high"
        elif score >= 0.40:
            confidence = "medium"
        else:
            confidence = "low"
        if hat_set_down:
            confidence = "low" if confidence == "medium" else confidence

        return {
            "event_id": str(uuid.uuid4()),
            "hat_id": HAT_ID,
            "type": etype,
            "timestamp": iso_local_now(),
            "priority": priority,
            "worn_before": p.worn_before,
            "worn_after": worn_after,
            "worn_confidence": p.worn_conf_before,
            "hat_possibly_set_down": hat_set_down,
            "sensor": {
                "peak_accel_g": round(p.peak_g, 3),
                "peak_gyro_dps": round(p.peak_gyro, 2),
                "freefall_ms": round(p.freefall_ms, 1),
                "tilt_change_deg": round(tilt, 1),
                "still_after": still_after,
                "score": round(score, 3),
                "imu_trace": self._build_trace(p.trigger_t),
            },
            "confidence": confidence,
        }

    def _build_trace(self, trigger_t: float) -> List[List[float]]:
        """Downsample buffer to ~50 Hz, t_ms relative to trigger, -2..+3 s."""
        t0, t1 = trigger_t - TRACE_PRE_S, trigger_t + TRACE_POST_S
        step = 1.0 / TRACE_HZ
        out: List[List[float]] = []
        next_t = t0
        for x in self.buffer:
            if x.t < t0 or x.t > t1:
                continue
            if x.t + 1e-9 >= next_t:
                out.append([
                    round((x.t - trigger_t) * 1000.0, 1),
                    round(x.ax, 3),
                    round(x.ay, 3),
                    round(x.az, 3),
                ])
                next_t += step
        return out


# =============================================================================
# Event hook
# =============================================================================

def default_on_event(event: dict) -> None:
    """Print and append JSONL. Replace this later with an HTTP POST."""
    line = json.dumps(event)
    print(f"\nEVENT {line}\n")
    try:
        with EVENTS_PATH.open("a") as f:
            f.write(line + "\n")
    except OSError as e:
        print(f"ERROR writing {EVENTS_PATH}: {e}", file=sys.stderr)


def on_event(event: dict) -> None:
    """Public hook — swap the body for a backend POST when ready."""
    default_on_event(event)


# =============================================================================
# Modes
# =============================================================================

def cmd_record(label: str, seconds: float) -> int:
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RECORDINGS_DIR / f"{label}_{stamp}.csv"

    for n in (3, 2, 1):
        print(f"{n}...")
        time.sleep(1)
    print("Recording!")

    rows: List[str] = ["t,ax,ay,az,gx,gy,gz\n"]
    last_status = [0.0]
    count = [0]

    def cb(s: Sample) -> None:
        rows.append(
            f"{s.t:.4f},{s.ax:.5f},{s.ay:.5f},{s.az:.5f},"
            f"{s.gx:.3f},{s.gy:.3f},{s.gz:.3f}\n"
        )
        count[0] += 1
        if s.t - last_status[0] >= 1.0:
            print(
                f"t={s.t:5.1f}s  acc=({s.ax:+.2f},{s.ay:+.2f},{s.az:+.2f}) "
                f"|a|={s.accel_mag:.2f}  |g|={s.gyro_mag:.1f}  n={count[0]}"
            )
            last_status[0] = s.t

    try:
        sample_loop(seconds, cb)
    except KeyboardInterrupt:
        print("\nStopped early.")
    path.write_text("".join(rows))
    print(f"Wrote {count[0]} samples → {path}")
    return 0


def cmd_calibrate_worn() -> int:
    print("=== Worn calibration ===")
    print("Put the hard hat ON YOUR HEAD and sit as still as you can.")
    input(f"Press Enter to start a {CALIB_PHASE_S:.0f}s worn recording... ")
    worn_stds = _collect_gyro_std_windows(CALIB_PHASE_S)
    worn_med = statistics.median(worn_stds) if worn_stds else 12.0
    print(f"Worn phase: {len(worn_stds)} windows, median gyro_std = {worn_med:.3f} dps")

    print("\nNow place the hat on a TABLE and leave it untouched.")
    input(f"Press Enter to start a {CALIB_PHASE_S:.0f}s table recording... ")
    table_stds = _collect_gyro_std_windows(CALIB_PHASE_S)
    table_med = statistics.median(table_stds) if table_stds else 1.5
    print(f"Table phase: {len(table_stds)} windows, median gyro_std = {table_med:.3f} dps")

    # 30% worn / 70% table → closer to the quiet table floor than the midpoint
    threshold = 0.30 * worn_med + 0.70 * table_med
    data = {
        "worn_gyro_std_median": round(worn_med, 4),
        "table_gyro_std_median": round(table_med, 4),
        "worn_threshold_gyro_std": round(threshold, 4),
        "calibrated_at": iso_local_now(),
    }
    save_calib(data)
    print("\nSaved", CALIB_PATH)
    print(json.dumps(data, indent=2))
    print(
        f"\nThreshold {threshold:.3f} dps "
        f"(= 0.3*{worn_med:.3f} + 0.7*{table_med:.3f})"
    )
    return 0


def _collect_gyro_std_windows(duration_s: float) -> List[float]:
    """Record for duration_s and return gyro_mag std-dev for each 1 s window."""
    samples: List[Sample] = []

    def cb(s: Sample) -> None:
        samples.append(s)
        if int(s.t) != int(s.t - SAMPLE_DT) and int(s.t) > 0:
            print(f"  ... {int(s.t)}/{int(duration_s)} s", end="\r")

    sample_loop(duration_s, cb)
    print()
    stds: List[float] = []
    if not samples:
        return stds
    t0 = samples[0].t
    while t0 + CALIB_WINDOW_S <= samples[-1].t + 1e-9:
        window = [x.gyro_mag for x in samples if t0 <= x.t < t0 + CALIB_WINDOW_S]
        if len(window) >= 10:
            stds.append(_std(window))
        t0 += CALIB_WINDOW_S
    return stds


def cmd_monitor() -> int:
    calib = load_calib()
    thr = float(calib.get("worn_threshold_gyro_std", DEFAULT_WORN_GYRO_STD))
    print(f"Monitor starting (worn_threshold={thr:.3f} dps). Ctrl+C to stop.")
    det = FallDetector(worn_threshold=thr, on_event=on_event)
    last_print = [0.0]

    def cb(s: Sample) -> None:
        det.push(s)
        if s.t - last_print[0] >= 1.0:
            st = det.last_status
            print(
                f"state={st['state']:10s}  |a|={st['accel_mag']:.2f}  "
                f"worn={st['worn']}  worn_conf={st['worn_confidence']}"
            )
            last_print[0] = s.t

    try:
        sample_loop(None, cb)
    except KeyboardInterrupt:
        print("\nMonitor stopped.")
    return 0


# =============================================================================
# Self-test (synthetic; no hardware)
# =============================================================================

def _synth(
    duration_s: float,
    accel: Tuple[float, float, float],
    gyro: Tuple[float, float, float],
    t0: float = 0.0,
    jitter_a: float = 0.0,
    jitter_g: float = 0.0,
) -> List[Sample]:
    out: List[Sample] = []
    n = int(duration_s * SAMPLE_HZ)
    for i in range(n):
        t = t0 + i * SAMPLE_DT
        # Deterministic tiny jitter from index (reproducible, no random dep needed)
        ja = jitter_a * math.sin(i * 0.37)
        jg = jitter_g * math.cos(i * 0.21)
        out.append(Sample(
            t=t,
            ax=accel[0] + ja,
            ay=accel[1] + ja * 0.5,
            az=accel[2],
            gx=gyro[0] + jg,
            gy=gyro[1] + jg * 0.5,
            gz=gyro[2],
        ))
    return out


def _run_scenario(name: str, samples: List[Sample], expected: str) -> Tuple[str, str, str, bool]:
    """
    expected: event type string, or 'none', or 'set_down' (any event with hat_possibly_set_down).
    """
    # Quiet table-like threshold so stillness → not_worn within hysteresis
    det = FallDetector(worn_threshold=3.0, on_event=lambda e: None)
    events: List[dict] = []
    for s in samples:
        ev = det.push(s)
        if ev is not None:
            events.append(ev)

    if expected == "none":
        ok = len(events) == 0
        got = "none" if ok else events[0]["type"]
    elif expected == "set_down":
        ok = any(e.get("hat_possibly_set_down") for e in events)
        got = (
            f"{events[0]['type']}+set_down"
            if events and events[0].get("hat_possibly_set_down")
            else (events[0]["type"] if events else "none")
        )
    else:
        ok = any(e["type"] == expected for e in events)
        got = events[0]["type"] if events else "none"

    return name, expected, got, ok


def _scenario_clean_fall() -> List[Sample]:
    parts: List[Sample] = []
    t = 0.0
    # worn baseline with some micro-motion so worn_before=True
    chunk = _synth(2.0, (0.05, 0.02, 0.98), (8, 5, 4), t0=t, jitter_g=3)
    parts += chunk
    t = parts[-1].t + SAMPLE_DT
    # free-fall ~250 ms at ~0.1 g
    chunk = _synth(0.25, (0.06, 0.05, 0.05), (20, 15, 10), t0=t)
    parts += chunk
    t = parts[-1].t + SAMPLE_DT
    # 6 g impact
    chunk = _synth(0.04, (0.2, 0.1, -6.0), (80, 60, 40), t0=t)
    parts += chunk
    t = parts[-1].t + SAMPLE_DT
    # still ~1 g for 4 s (long enough for worn→not_worn, but type=fall wins)
    chunk = _synth(4.0, (0.02, 0.01, 0.99), (0.4, 0.3, 0.2), t0=t, jitter_a=0.01, jitter_g=0.2)
    parts += chunk
    return parts


def _scenario_tumbling() -> List[Sample]:
    parts: List[Sample] = []
    t = 0.0
    parts += _synth(2.0, (0.1, 0.05, 0.97), (10, 8, 6), t0=t, jitter_g=4)
    t = parts[-1].t + SAMPLE_DT
    # hard tumbling impact + high gyro, no free-fall dip
    parts += _synth(0.05, (2.0, 2.5, -4.5), (300, 220, 160), t0=t)  # |a|~5.5, |g|~400
    t = parts[-1].t + SAMPLE_DT
    parts += _synth(4.0, (0.03, 0.02, 0.98), (0.5, 0.4, 0.3), t0=t, jitter_a=0.01, jitter_g=0.2)
    return parts


def _scenario_bump_continue() -> List[Sample]:
    """Hard bump while still moving — classified near_fall, but NOT logged."""
    parts: List[Sample] = []
    t = 0.0
    parts += _synth(2.0, (0.05, 0.02, 0.98), (12, 9, 7), t0=t, jitter_g=5)
    t = parts[-1].t + SAMPLE_DT
    parts += _synth(0.04, (1.0, 1.0, 5.5), (40, 30, 20), t0=t)  # |a|~5.7
    t = parts[-1].t + SAMPLE_DT
    parts += _synth(4.0, (0.2, 0.1, 1.1), (40, 35, 25), t0=t, jitter_a=0.15, jitter_g=15)
    return parts


def _scenario_set_down() -> List[Sample]:
    """Soft set-down — must not trigger / not log."""
    parts: List[Sample] = []
    t = 0.0
    parts += _synth(2.0, (0.05, 0.02, 0.98), (10, 8, 5), t0=t, jitter_g=4)
    t = parts[-1].t + SAMPLE_DT
    parts += _synth(0.04, (0.4, 0.3, 3.15), (25, 20, 15), t0=t)  # |a|~3.19 < IMPACT_G
    t = parts[-1].t + SAMPLE_DT
    parts += _synth(4.5, (0.01, 0.01, 1.00), (0.2, 0.15, 0.1), t0=t, jitter_a=0.005, jitter_g=0.1)
    return parts


def _scenario_knocked_off() -> List[Sample]:
    parts: List[Sample] = []
    t = 0.0
    parts += _synth(2.0, (0.05, 0.02, 0.98), (11, 7, 5), t0=t, jitter_g=4)
    t = parts[-1].t + SAMPLE_DT
    parts += _synth(0.05, (2.5, 1.2, -5.0), (120, 90, 60), t0=t)  # |a|~5.7
    t = parts[-1].t + SAMPLE_DT
    parts += _synth(4.5, (0.98, 0.05, 0.05), (0.3, 0.2, 0.2), t0=t, jitter_a=0.01, jitter_g=0.15)
    return parts


def _scenario_walking() -> List[Sample]:
    out: List[Sample] = []
    n = int(6.0 * SAMPLE_HZ)
    for i in range(n):
        t = i * SAMPLE_DT
        # periodic ~1.5 g peaks (below IMPACT_G)
        phase = math.sin(2 * math.pi * 1.6 * t)
        az = 1.0 + 0.5 * max(0.0, phase)  # peaks ~1.5
        out.append(Sample(t, 0.05, 0.05, az, 15 + 10 * phase, 10, 8))
    return out


def _scenario_sit_hard() -> List[Sample]:
    parts: List[Sample] = []
    t = 0.0
    parts += _synth(2.0, (0.05, 0.02, 0.98), (8, 6, 4), t0=t, jitter_g=3)
    t = parts[-1].t + SAMPLE_DT
    parts += _synth(0.05, (0.2, 0.2, 2.48), (30, 20, 15), t0=t)  # |a|~2.5 < 3.0
    t = parts[-1].t + SAMPLE_DT
    parts += _synth(3.0, (0.05, 0.02, 0.98), (10, 8, 5), t0=t, jitter_g=4)
    return parts


def cmd_selftest() -> int:
    # Expectations are what gets *logged* (near_fall / soft set-down → none)
    scenarios = [
        ("a_clean_fall", _scenario_clean_fall, "fall"),
        ("b_tumbling_fall", _scenario_tumbling, "fall"),
        ("c_bump_continue", _scenario_bump_continue, "none"),
        ("d_hat_set_down", _scenario_set_down, "none"),
        ("e_hat_knocked_off", _scenario_knocked_off, "hat_removed"),
        ("f_walking", _scenario_walking, "none"),
        ("g_sit_hard", _scenario_sit_hard, "none"),
    ]
    print(f"{'scenario':22s}  {'expected':12s}  {'got':16s}  result")
    print("-" * 64)
    failures = 0
    for name, gen, expected in scenarios:
        _n, exp, got, ok = _run_scenario(name, gen(), expected)
        mark = "PASS" if ok else "FAIL"
        if not ok:
            failures += 1
        print(f"{name:22s}  {exp:12s}  {got:16s}  {mark}")
    print("-" * 64)
    print(f"{'ALL PASS' if failures == 0 else f'{failures} FAILED'}")
    return 0 if failures == 0 else 1


# =============================================================================
# CLI
# =============================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="MPU-6050 hard-hat fall / impact detector")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("record", help="Record raw IMU CSV")
    r.add_argument("--label", required=True)
    r.add_argument("--seconds", type=float, required=True)

    sub.add_parser("calibrate-worn", help="Guided worn vs table calibration")
    sub.add_parser("monitor", help="Live fall / impact monitor")
    sub.add_parser("selftest", help="Synthetic detector unit tests")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "record":
            return cmd_record(args.label, args.seconds)
        if args.cmd == "calibrate-worn":
            return cmd_calibrate_worn()
        if args.cmd == "monitor":
            return cmd_monitor()
        if args.cmd == "selftest":
            return cmd_selftest()
        print(f"Unknown command: {args.cmd}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
