"""Wear-gated fall detector and the ImuSample type.

The sensor driver lives in `hat/minimu9.py` (Pololu MinIMU-9 v3). The previous
MPU-6050 driver is archived in `hat/archive/mpu6050.py`.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, List, Optional, Tuple

from hat import config as cfg
from hat.wear_state import WearState


@dataclass
class ImuSample:
    t: float
    ax: float
    ay: float
    az: float
    # None until the gyro zero-rate calibration has finished (MotionState handles None)
    gx: Optional[float]
    gy: Optional[float]
    gz: Optional[float]

    @property
    def mag(self) -> float:
        return math.sqrt(self.ax * self.ax + self.ay * self.ay + self.az * self.az)


@dataclass
class Detection:
    type: str  # always "fall" (impact path removed)
    peak_accel_g: float
    freefall_ms: float
    confidence: str
    imu_trace: List[List[float]]
    helmet_worn_at_onset: bool = False
    helmet_worn_lookback_s: float = 0.0
    wear_on_fraction: float = 0.0
    suppressed: bool = False
    suppress_reason: str = ""
    onset_age_s: float = 0.0  # detection time minus fall onset (event timestamp backdating)
    knocked_off: bool = False


class FallImpactDetector:
    """
    Person-fall signature (IMU) + optional ToF wear gate.

    Everything actionable is a `fall` (no separate impact type).

      idle --(|a| < FREEFALL_G for FREEFALL_MIN_S)--> freefall
      freefall --(|a| >= IMPACT_AFTER_FF_G within window)--> wait_still
      freefall --(settles to ~1 g after >= SOFT_LANDING_AFTER_FF_S)--> wait_still
      idle --(|a| >= IMPACT_G, no freefall)--> wait_still
      wait_still --(|a|~1 g for STILL_HOLD_S)--> check head tilt vs pre-fall
          gravity (>= FALL_TILT_MIN_DEG unless freefall was long) --> FALL
      wait_still (after an impact) --(ToF: hat left the head, raw not-near
          run >= KNOCK_OFF_RUN_S that began at fall onset)--> FALL, no
          tilt/stillness needed (hat knocked off, lands/rolls anywhere)

    Every emitted fall has confidence "high"; unverifiable candidates are dropped.

    Not a fall: a hit/jump after which the wearer keeps moving (no stillness
    within FALL_STILL_DEADLINE_S) or ends up in the same orientation.

    After an event the detector is disarmed until the signal has been quiet
    (~1 g) for REARM_QUIET_S, plus IMU_COOLDOWN_S, so one fall = one event.

    Samples are never trusted while the sensor config may be wrong:
    `config_lost()` purges the recent buffer and disarms, and a window whose
    resting magnitude is physically implausible (> IMU_IMPLAUSIBLE_G) blocks
    detection.

    If wear_state is set and WEAR_GATE_ENABLE: only emit when helmet was
    ON beforehand. No IMU hat_removed path.
    """

    def __init__(self, wear_state: Optional[WearState] = None) -> None:
        # Shared mutable float holder: haptics calls note_buzz()
        self._last_buzz_holder = {"t": 0.0}
        self.wear_state = wear_state
        self.suppressed_falls = 0
        self.suppressed_impacts = 0  # kept for summary compat; always 0
        self.config_purges = 0
        n = int(cfg.IMU_BUFFER_S * cfg.IMU_SAMPLE_HZ) + 8
        self._buf: Deque[ImuSample] = deque(maxlen=n)
        self._state = "idle"
        self._ff_start: Optional[float] = None
        self._ff_ms = 0.0
        self._impact_t: Optional[float] = None
        self._impacted = False
        self._onset_t: Optional[float] = None
        self._knock_t: Optional[float] = None
        self._tilt_rejected = False
        self._peak = 0.0
        self._last_tilt: Optional[float] = None
        self._still_start: Optional[float] = None
        self._cooldown_until = 0.0
        self._armed = True
        self._quiet_start: Optional[float] = None
        self._plaus: Deque[Tuple[float, bool]] = deque()
        self._plaus_big = 0
        self.implausible = False
        self._last_error_log = 0.0

    @property
    def last_buzz_holder(self) -> dict:
        return self._last_buzz_holder

    def note_buzz(self) -> None:
        self._last_buzz_holder["t"] = time.perf_counter()

    def status(self, now: float) -> dict:
        """Read-only snapshot of the detector state for telemetry (called from another thread; racy but harmless)."""
        st = self._state
        return {
            "armed": bool(self._armed),
            "candidate": st != "idle",
            "waiting_still": st == "wait_still",
            "cooldown": now < self._cooldown_until,
            "state": st,
        }

    # -- config-loss handling -------------------------------------------
    def config_lost(self, now: Optional[float] = None) -> None:
        """Driver found/repaired a wrong sensor config: distrust recent data."""
        now = time.perf_counter() if now is None else now
        cutoff = now - cfg.IMU_CONFIG_LOSS_PURGE_S
        while self._buf and self._buf[-1].t >= cutoff:
            self._buf.pop()
        self.config_purges += 1
        self._reset_state()
        self._armed = False
        self._quiet_start = None
        self._cooldown_until = max(self._cooldown_until, now + cfg.IMU_SETTLE_AFTER_REINIT_S)
        self._plaus.clear()
        self._plaus_big = 0
        self.implausible = False

    def _track_plausibility(self, now: float, mag: float) -> None:
        big = mag > cfg.IMU_IMPLAUSIBLE_G
        self._plaus.append((now, big))
        self._plaus_big += big
        while self._plaus and now - self._plaus[0][0] > cfg.IMU_IMPLAUSIBLE_WINDOW_S:
            self._plaus_big -= self._plaus.popleft()[1]
        span = now - self._plaus[0][0]
        self.implausible = (
            span >= 0.5 * cfg.IMU_IMPLAUSIBLE_WINDOW_S
            and self._plaus_big >= 0.8 * len(self._plaus)
        )

    # -- main entry ------------------------------------------------------
    def push(self, sample: ImuSample) -> Optional[Detection]:
        self._buf.append(sample)
        now = sample.t
        mag = sample.mag

        self._track_plausibility(now, mag)
        if mag >= cfg.IMPACT_G and self.wear_state is not None:
            self.wear_state.note_impact(now)
        if self.implausible:
            # e.g. a resting head cannot read ~8 g: the data is garbage.
            self._reset_state()
            self._armed = False
            self._quiet_start = None
            return None

        # Ignore IMU for a short window after haptic pulse
        if now - self._last_buzz_holder["t"] < cfg.IMU_IGNORE_AFTER_BUZZ_S:
            self._still_start = None
            return None

        if not self._armed:
            if abs(mag - 1.0) <= cfg.REARM_BAND_G:
                if self._quiet_start is None:
                    self._quiet_start = now
                elif (now - self._quiet_start) >= cfg.REARM_QUIET_S and now >= self._cooldown_until:
                    self._armed = True
            else:
                self._quiet_start = None
            return None
        if now < self._cooldown_until:
            return None

        if self._state == "idle":
            if mag < cfg.FREEFALL_G:
                if self._ff_start is None:
                    self._ff_start = now
                elif (now - self._ff_start) >= cfg.FREEFALL_MIN_S:
                    self._state = "freefall"
                    self._onset_t = self._ff_start
                    self._ff_ms = (now - self._ff_start) * 1000.0
                    self._peak = mag
            else:
                self._ff_start = None
                if mag >= cfg.IMPACT_G:
                    # Hard hit without freefall: only a fall if the wearer then
                    # lies still in a different orientation (checked below).
                    self._peak = mag
                    self._ff_ms = 0.0
                    self._onset_t = now
                    self._enter_wait_still(now, impacted=True)
            return None

        if self._state == "freefall":
            if mag < cfg.FREEFALL_G and self._ff_start is not None:
                self._ff_ms = max(self._ff_ms, (now - self._ff_start) * 1000.0)
            self._peak = max(self._peak, mag)
            if mag >= cfg.IMPACT_AFTER_FF_G:
                self._enter_wait_still(now, impacted=True)
                return None
            # Soft landing: left freefall and settled near 1 g without a spike
            if (
                self._ff_ms >= cfg.SOFT_LANDING_AFTER_FF_S * 1000.0
                and abs(mag - 1.0) <= cfg.STILL_BAND_G
            ):
                self._peak = max(self._peak, mag, 1.0)
                self._enter_wait_still(now, impacted=False)
                return None
            if self._ff_start and (now - self._ff_start) > cfg.IMPACT_AFTER_FF_WINDOW_S + cfg.FREEFALL_MIN_S:
                self._reset_state()
            return None

        if self._state == "wait_still":
            self._peak = max(self._peak, mag)
            assert self._impact_t is not None
            if self._impacted:
                ks = self._knocked_off(now)
                if ks is not None:
                    self._knock_t = ks
                    return self._emit_fall(now, confidence="high")
            if abs(mag - 1.0) <= cfg.STILL_BAND_G and not self._tilt_rejected:
                if self._still_start is None:
                    self._still_start = now
                elif (now - self._still_start) >= cfg.STILL_HOLD_S:
                    return self._confirm(now)
            else:
                self._still_start = None
            if (now - self._impact_t) >= cfg.FALL_STILL_DEADLINE_S:
                # Kept moving: hit / jump / stumble, not a person lying still.
                self._reset_state()
            return None

        return None

    def _enter_wait_still(self, now: float, impacted: bool) -> None:
        self._state = "wait_still"
        self._impact_t = now
        self._impacted = impacted
        self._still_start = None

    # -- orientation -----------------------------------------------------
    def _mean_vec(self, t_lo: float, t_hi: float, min_n: int) -> Optional[Tuple[float, float, float]]:
        sx = sy = sz = 0.0
        n = 0
        for s in self._buf:
            if t_lo <= s.t <= t_hi:
                sx += s.ax
                sy += s.ay
                sz += s.az
                n += 1
        if n < min_n:
            return None
        return sx / n, sy / n, sz / n

    def _tilt_change_deg(self, onset: float, still_start: float, now: float) -> Optional[float]:
        """Angle between pre-fall and post-fall gravity (head orientation change)."""
        ref = self._mean_vec(onset - cfg.FALL_REF_WINDOW_S, onset - 0.15, 10)
        post = self._mean_vec(still_start, now, 10)
        if ref is None or post is None:
            return None
        nr = math.sqrt(sum(c * c for c in ref))
        npost = math.sqrt(sum(c * c for c in post))
        if not (0.7 <= nr <= 1.3) or not (0.7 <= npost <= 1.3):
            return None  # reference not a clean gravity vector
        dot = sum(a * b for a, b in zip(ref, post)) / (nr * npost)
        return math.degrees(math.acos(max(-1.0, min(1.0, dot))))

    def _knock_possible(self) -> bool:
        return (
            cfg.KNOCK_OFF_ENABLE
            and self.wear_state is not None
            and getattr(cfg, "WEAR_GATE_ENABLE", True)
        )

    def _knocked_off(self, now: float) -> Optional[float]:
        """ToF says the hat left the head right after the fall started."""
        if (
            not cfg.KNOCK_OFF_ENABLE
            or self.wear_state is None
            or not getattr(cfg, "WEAR_GATE_ENABLE", True)
            or self._onset_t is None
        ):
            return None
        return self.wear_state.not_near_run_start(
            now,
            since_t=self._onset_t - cfg.KNOCK_OFF_PRE_S,
            min_run_s=cfg.KNOCK_OFF_RUN_S,
            max_stale_s=cfg.KNOCK_TOF_STALE_S,
        )

    def _confirm(self, now: float) -> Optional[Detection]:
        """Stillness reached: accept only if the head ended up re-oriented."""
        assert self._still_start is not None and self._onset_t is not None
        self._last_tilt = None
        if self._ff_ms >= cfg.FALL_TILT_EXEMPT_FF_S * 1000.0:
            return self._emit_fall(now, confidence="high")
        tilt = self._tilt_change_deg(self._onset_t, self._still_start, now)
        self._last_tilt = tilt
        if tilt is not None and tilt >= cfg.FALL_TILT_MIN_DEG:
            return self._emit_fall(now, confidence="high")
        # Same orientation / unverifiable: jump, bump, nod. Not a fall by the
        # orientation rule, but keep the candidate alive until the deadline so a
        # hat that was knocked off can still be confirmed by the ToF.
        if self._impacted and self._knock_possible():
            self._tilt_rejected = True
            self._still_start = None
        else:
            self._reset_state()
        return None

    def _wear_gate(self, onset_t: float) -> Tuple[bool, float, float, str]:
        """Return (allow, worn_at_onset, on_fraction, reason)."""
        if not getattr(cfg, "WEAR_GATE_ENABLE", True):
            return True, True, 1.0, "gate_disabled"
        if self.wear_state is None:
            # No ToF wired into detector → allow (bring-up / unit tests)
            return True, False, 0.0, "no_wear_state"
        lookback = getattr(cfg, "WEAR_GATE_LOOKBACK_S", 3.0)
        min_frac = getattr(cfg, "WEAR_GATE_MIN_ON_FRACTION", 0.5)
        g = self.wear_state.gate_fall(
            onset_t, lookback_s=lookback, min_on_fraction=min_frac
        )
        return g.allow, g.helmet_worn_at_onset, g.on_fraction, g.reason

    def _emit_fall(self, now: float, confidence: str) -> Optional[Detection]:
        min_peak = getattr(cfg, "FALL_MIN_PEAK_G", 5.0)
        if self._peak < min_peak:
            self._reset_state()
            return None
        # Short/no free fall: needs a hard hit plus a large re-orientation (knocked-off path has its own evidence).
        if self._peak > cfg.FALL_MAX_PEAK_G:
            self._reset_state()  # physically implausible here: bad bus data
            return None
        if self._knock_t is None and self._ff_ms < cfg.FALL_MIN_FREEFALL_MS:
            tilt = self._last_tilt
            if self._peak < cfg.FALL_HIT_MIN_PEAK_G or tilt is None or tilt < cfg.FALL_HIT_MIN_TILT_DEG:
                self._reset_state()
                return None
        onset = self._onset_t if self._onset_t is not None else now
        allow, worn_at, frac, reason = self._wear_gate(onset)
        lookback = getattr(cfg, "WEAR_GATE_LOOKBACK_S", 3.0)
        det = Detection(
            type="fall",
            peak_accel_g=self._peak,
            freefall_ms=self._ff_ms,
            confidence=confidence,
            imu_trace=self._trace_relative(now),
            helmet_worn_at_onset=worn_at,
            helmet_worn_lookback_s=lookback,
            wear_on_fraction=frac,
            suppressed=not allow,
            suppress_reason=reason if not allow else "",
            onset_age_s=max(0.0, now - min(onset, self._knock_t if self._knock_t is not None else onset)),
            knocked_off=self._knock_t is not None,
        )
        if not allow:
            self.suppressed_falls += 1
        # One fall = one event: disarm until the signal is quiet again.
        self._cooldown_until = now + cfg.IMU_COOLDOWN_S
        self._armed = False
        self._quiet_start = None
        self._reset_state()
        return det

    def _reset_state(self) -> None:
        self._state = "idle"
        self._ff_start = None
        self._ff_ms = 0.0
        self._impact_t = None
        self._impacted = False
        self._onset_t = None
        self._knock_t = None
        self._tilt_rejected = False
        self._peak = 0.0
        self._still_start = None

    def _trace_relative(self, t0: float) -> List[List[float]]:
        """Ring buffer as [t_ms, ax, ay, az] relative to event time."""
        out: List[List[float]] = []
        for s in self._buf:
            out.append([
                round((s.t - t0) * 1000.0, 1),
                round(s.ax, 3),
                round(s.ay, 3),
                round(s.az, 3),
            ])
        return out

    def rate_test(self, seconds: float = 2.0) -> Tuple[float, int]:
        """Return (hz, i2c_errors) over a short burst."""
        from hat.minimu9 import MiniIMU9  # lazy: minimu9 imports ImuSample from here

        imu = MiniIMU9()
        errors = 0
        n = 0
        t0 = time.perf_counter()
        try:
            while time.perf_counter() - t0 < seconds:
                try:
                    imu.read()
                    n += 1
                except OSError:
                    errors += 1
        finally:
            imu.close()
        elapsed = max(time.perf_counter() - t0, 1e-6)
        return n / elapsed, errors
