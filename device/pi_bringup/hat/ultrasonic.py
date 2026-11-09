"""HC-SR04 driver (gpiozero + lgpio busy-wait) and approach detector."""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional, Tuple

try:  # not installed on a laptop; the detector below is pure Python and testable without it
    from gpiozero import DigitalInputDevice, DigitalOutputDevice
except ImportError:  # pragma: no cover
    DigitalInputDevice = DigitalOutputDevice = None

from hat import config as cfg


class HCSR04:
    """Single HC-SR04 with busy-wait echo timing (~24 ms timeout)."""

    def __init__(self, trig_gpio: int, echo_gpio: int, name: str = ""):
        self.name = name
        self.trig = DigitalOutputDevice(trig_gpio, active_high=True, initial_value=False)
        self.echo = DigitalInputDevice(echo_gpio, pull_up=False)

    def distance_m(self) -> Optional[float]:
        """Return metres, or None on timeout / out of range (not an error)."""
        self.trig.off()
        time.sleep(0.000002)
        self.trig.on()
        time.sleep(0.00001)
        self.trig.off()

        t0 = time.perf_counter()
        while self.echo.value == 0:
            if time.perf_counter() - t0 > cfg.US_ECHO_TIMEOUT_S:
                return None
        rise = time.perf_counter()

        while self.echo.value == 1:
            if time.perf_counter() - rise > cfg.US_ECHO_TIMEOUT_S:
                return None
        fall = time.perf_counter()

        dist = (fall - rise) * cfg.US_SOUND_MPS / 2.0
        if dist < cfg.US_MIN_RANGE_M or dist > cfg.US_MAX_RANGE_M:
            return None
        return dist

    def close(self) -> None:
        self.trig.close()
        self.echo.close()


@dataclass
class ApproachEvent:
    side: str
    distance_m: float
    closing_speed_mps: float
    reason: str = "close_and_fast"


class MotionState:
    """Wearer-motion summary from the IMU (written by the IMU thread, read by the US thread).

    Deliberately NOT a velocity: a consumer MEMS accelerometer integrated to velocity drifts
    within about a second, and the head rotates independently of the body. Only two robust
    things are extracted:
      * gyro_dps : instantaneous |gyro|. Fast head rotation sweeps the beam over surfaces at
                   different ranges, which looks like a huge closing speed.
      * moving   : RMS of | |a|-1 g | over ~0.3 s (walking bounce / body motion), or any gyro rate
                   above 20 dps in the last 0.5 s. Used to RAISE the closing-speed bar, not to subtract anything.
    Plain floats; one writer, one reader, so the GIL is enough.
    """

    def __init__(self) -> None:
        self.last_t: Optional[float] = None
        self.gyro_dps = 0.0
        self._dyn2 = 0.0
        self.moving_until = -1e9
        self.gate_until = -1e9

    def push(self, t: float, ax: float, ay: float, az: float,
             gx: Optional[float], gy: Optional[float], gz: Optional[float]) -> None:
        dt = 0.005 if self.last_t is None else min(max(t - self.last_t, 0.0), 0.1)
        self.last_t = t
        a = 1.0 - math.exp(-dt / cfg.US_MOTION_TAU_S) if cfg.US_MOTION_TAU_S > 0 else 1.0
        dyn = math.sqrt(ax * ax + ay * ay + az * az) - 1.0
        self._dyn2 += a * (dyn * dyn - self._dyn2)
        if gx is None or gy is None or gz is None:
            self.gyro_dps = 0.0  # accel-only IMU: head rotation unobservable
        else:
            self.gyro_dps = math.sqrt(gx * gx + gy * gy + gz * gz)
        if self.gyro_dps > cfg.US_MOVING_GYRO_DPS:
            self.moving_until = t + cfg.US_MOVING_HOLD_S
        if self.gyro_dps > cfg.US_GYRO_GATE_DPS:
            self.gate_until = t + cfg.US_GYRO_HOLDOFF_S

    def valid(self, t: float) -> bool:
        return self.last_t is not None and (t - self.last_t) <= cfg.US_MOTION_STALE_S

    def gated(self, t: float) -> bool:
        return self.valid(t) and t <= self.gate_until

    def moving(self, t: float) -> bool:
        if not self.valid(t):
            return False
        return math.sqrt(self._dyn2) > cfg.US_MOVING_DYN_G or t <= self.moving_until

    @property
    def dyn_rms_g(self) -> float:
        return math.sqrt(self._dyn2)


def _fit(hist) -> Tuple[float, float]:
    """Least-squares line through (t, range). Returns (closing speed m/s, RMS residual m)."""
    n = len(hist)
    mt = sum(t for t, _ in hist) / n
    mr = sum(r for _, r in hist) / n
    den = sum((t - mt) ** 2 for t, _ in hist)
    if den <= 0.0:
        return 0.0, 0.0
    slope = sum((t - mt) * (r - mr) for t, r in hist) / den
    res = math.sqrt(sum((r - (mr + slope * (t - mt))) ** 2 for t, r in hist) / n)
    return -slope, res


class ApproachDetector:
    """
    Approach detector for one rear HC-SR04 (rewritten 2026-11-08 after 235 false
    line_of_fire events in 8 min on a static ~0.78 m wall).

    Old rule: one two-sample difference (80 ms apart) >= 0.40 m/s inside 1.8 m, so
    a single 3.2 cm range wobble fired the buzzer. New rule:

      normal path ("close_and_fast")
        last 4 valid samples (same sensor, no gap > 0.2 s), and ALL of:
          * every step closes by >= 0.5 * v_req * dt          (monotonic, not one spike)
          * least-squares closing speed >= v_req              (1.0 m/s; 2.0 m/s while the wearer moves)
          * RMS residual about the line <= 4 cm               (straight approach, not a sweep)
          * net drop >= 15 cm
          * range <= 1.8 m and range / speed <= 1.5 s         (time to collision)
      fast path ("instant_drop")
        last two steps each >= 3 m/s (>= 25 cm per 80 ms), range <= 2.5 m, TTC <= 1.2 s.
        Fires after 3 samples (~0.16 s), for a swung/thrown object.

    Wearer motion (see MotionState): head rotation above 40 dps drops the track and holds off
    0.25 s; wearer-moving raises v_req from 1.0 to 2.0 m/s on the normal path. Closing speed
    reported is the RELATIVE speed, not ego-subtracted (ego velocity is not observable well enough).
    Readings below 25 cm (own body / hat brim) are ignored.
    """

    def __init__(self, side: str):
        self.side = side
        self._hist: Deque[Tuple[float, float]] = deque(maxlen=cfg.US_TRACK_SAMPLES)
        self._last_good_t: Optional[float] = None
        self._cooldown_until = 0.0
        self.last_distance: Optional[float] = None
        self.last_closing: float = 0.0
        self.track_ok: bool = False
        self.last_gated: bool = False

    def update(self, t: float, raw: Optional[float],
               motion: Optional[MotionState] = None) -> Optional[ApproachEvent]:
        if motion is not None and motion.gated(t):
            self._break_track()
            self.last_gated = True
            return None
        self.last_gated = False

        if raw is not None and raw < cfg.US_IGNORE_BELOW_M:
            raw = None
        if raw is None:
            if (self._last_good_t is not None
                    and (t - self._last_good_t) > cfg.US_TRACK_BREAK_S):
                self._break_track()
            return None

        if self._last_good_t is not None and (t - self._last_good_t) > cfg.US_TRACK_BREAK_S:
            self._break_track()

        if self._hist:
            t_prev, d_prev = self._hist[-1]
            dt_prev = t - t_prev
            if dt_prev > 0 and abs(d_prev - raw) / dt_prev > cfg.US_MAX_PLAUSIBLE_MPS:
                return None  # teleport: do not let it into the window

        self._hist.append((t, raw))
        self._last_good_t = t
        self.last_distance = raw
        self.track_ok = True

        speed = resid = 0.0
        if len(self._hist) >= 3:
            speed, resid = _fit(list(self._hist)[-cfg.US_TRACK_SAMPLES:])
        self.last_closing = speed

        if t < self._cooldown_until:
            return None
        moving = motion.moving(t) if motion is not None else False
        reason = self._decide(raw, moving)
        if reason is None:
            return None
        self._cooldown_until = t + cfg.US_COOLDOWN_S
        sp = self._last_speed
        return ApproachEvent(side=self.side, distance_m=round(raw, 3),
                             closing_speed_mps=round(sp, 2), reason=reason)

    def _decide(self, rng: float, moving: bool) -> Optional[str]:
        h = list(self._hist)
        self._last_speed = 0.0
        # fast path: last two steps each very fast
        if len(h) >= 3 and rng <= cfg.US_FAST_RANGE_M:
            sp = []
            for (t0, r0), (t1, r1) in zip(h[-3:], h[-2:]):
                dt = t1 - t0
                sp.append((r0 - r1) / dt if dt >= cfg.US_MIN_SPEED_DT_S else 0.0)
            if min(sp) >= cfg.US_FAST_MPS:
                v = sum(sp) / len(sp)
                if rng / v <= cfg.US_FAST_TTC_MAX_S:
                    self._last_speed = v
                    return "instant_drop"
        # normal path
        if len(h) < cfg.US_TRACK_SAMPLES or rng > cfg.US_WARN_DISTANCE_M:
            return None
        v_req = cfg.US_MOVING_MIN_MPS if moving else cfg.US_MIN_OBJ_MPS
        for (t0, r0), (t1, r1) in zip(h, h[1:]):
            dt = t1 - t0
            if dt < cfg.US_MIN_SPEED_DT_S or (r0 - r1) < cfg.US_MIN_STEP_FRACTION * v_req * dt:
                return None
        if h[0][1] - h[-1][1] < cfg.US_MIN_NET_DROP_M:
            return None
        v, resid = _fit(h)
        if v < v_req or resid > cfg.US_MAX_RESID_M or rng / v > cfg.US_TTC_MAX_S:
            return None
        self._last_speed = v
        return "close_and_fast"

    _last_speed = 0.0

    def _break_track(self) -> None:
        self._hist.clear()
        self._last_good_t = None
        self.last_distance = None
        self.last_closing = 0.0
        self.track_ok = False
