"""Frozen copy of the pre-2026-11-08 fall detector (for replay comparison only).

Do not use in production. Constants are frozen to the values that produced the
53 false falls logged on 2026-11-08 19:22-19:24.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Deque, List, Optional, Tuple

from hat.imu import ImuSample
from hat.wear_state import WearState

L = SimpleNamespace(
    IMU_SAMPLE_HZ=200.0, IMU_BUFFER_S=2.0,
    FREEFALL_G=0.45, FREEFALL_MIN_S=0.050, IMPACT_AFTER_FF_G=1.6,
    IMPACT_AFTER_FF_WINDOW_S=2.0, STILL_BAND_G=0.20, STILL_HOLD_S=0.300,
    FALL_STILL_DEADLINE_S=3.0, FALL_MIN_PEAK_G=1.0, IMPACT_G=3.0,
    SOFT_LANDING_AFTER_FF_S=0.080, IMU_COOLDOWN_S=2.0,
    IMU_IGNORE_AFTER_BUZZ_S=0.250, WEAR_GATE_ENABLE=True,
    WEAR_GATE_LOOKBACK_S=3.0, WEAR_GATE_MIN_ON_FRACTION=0.50,
)


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


class LegacyFallDetector:
    """
    Person-fall signature (IMU) + optional ToF wear gate.

    Everything actionable is a `fall` (no separate impact type):
      idle → ( |a| < FREEFALL_G for FREEFALL_MIN_S ) → freefall
           → hard land / soft settle → FALL
      idle → ( |a| >= IMPACT_G ) → FALL (hard hit without long freefall)

    If wear_state is set and WEAR_GATE_ENABLE: only emit when helmet was
    ON beforehand. No IMU hat_removed path.
    """

    def __init__(self, wear_state: Optional[WearState] = None) -> None:
        # Shared mutable float holder: haptics calls note_buzz()
        self._last_buzz_holder = {"t": 0.0}
        self.wear_state = wear_state
        self.suppressed_falls = 0
        self.suppressed_impacts = 0  # kept for summary compat; always 0
        n = int(L.IMU_BUFFER_S * L.IMU_SAMPLE_HZ) + 8
        self._buf: Deque[ImuSample] = deque(maxlen=n)
        self._state = "idle"
        self._ff_start: Optional[float] = None
        self._ff_ms = 0.0
        self._impact_t: Optional[float] = None
        self._peak = 0.0
        self._still_start: Optional[float] = None
        self._cooldown_until = 0.0
        self._last_error_log = 0.0

    @property
    def last_buzz_holder(self) -> dict:
        return self._last_buzz_holder

    def note_buzz(self) -> None:
        self._last_buzz_holder["t"] = time.perf_counter()

    def push(self, sample: ImuSample) -> Optional[Detection]:
        self._buf.append(sample)
        now = sample.t

        # Ignore IMU for a short window after haptic pulse
        if now - self._last_buzz_holder["t"] < L.IMU_IGNORE_AFTER_BUZZ_S:
            return None
        if now < self._cooldown_until:
            return None

        mag = sample.mag

        if self._state == "idle":
            if mag < L.FREEFALL_G:
                if self._ff_start is None:
                    self._ff_start = now
                elif (now - self._ff_start) >= L.FREEFALL_MIN_S:
                    self._state = "freefall"
                    self._ff_ms = (now - self._ff_start) * 1000.0
                    self._peak = mag
            else:
                self._ff_start = None
                # Hard hit with little/no freefall → still a fall (no impact type)
                if mag >= L.IMPACT_G:
                    self._peak = mag
                    self._ff_ms = 0.0
                    self._ff_start = now
                    return self._emit_fall(now, confidence="high")
            return None

        if self._state == "freefall":
            self._ff_ms = max(self._ff_ms, (now - (self._ff_start or now)) * 1000.0)
            self._peak = max(self._peak, mag)
            if mag >= L.IMPACT_AFTER_FF_G:
                self._state = "wait_still"
                self._impact_t = now
                self._still_start = None
                return None
            # Soft landing: left freefall and settled near 1 g without a hard spike
            soft_need = getattr(L, "SOFT_LANDING_AFTER_FF_S", 0.080)
            if (
                self._ff_ms >= soft_need * 1000.0
                and abs(mag - 1.0) <= L.STILL_BAND_G
            ):
                self._peak = max(self._peak, mag, 1.0)
                return self._emit_fall(now, confidence="low")
            if self._ff_start and (now - self._ff_start) > L.IMPACT_AFTER_FF_WINDOW_S + L.FREEFALL_MIN_S:
                self._reset_state()
            return None

        if self._state == "wait_still":
            self._peak = max(self._peak, mag)
            assert self._impact_t is not None
            if abs(mag - 1.0) <= L.STILL_BAND_G:
                if self._still_start is None:
                    self._still_start = now
                elif (now - self._still_start) >= L.STILL_HOLD_S:
                    return self._emit_fall(now, confidence="high")
            else:
                self._still_start = None

            if (now - self._impact_t) >= L.FALL_STILL_DEADLINE_S:
                if self._peak >= getattr(L, "FALL_MIN_PEAK_G", 5.0):
                    return self._emit_fall(now, confidence="low")
                self._reset_state()
            return None

        return None

    def _wear_gate(self, onset_t: float) -> Tuple[bool, float, float, str]:
        """Return (allow, worn_at_onset, on_fraction, reason)."""
        if not getattr(L, "WEAR_GATE_ENABLE", True):
            return True, True, 1.0, "gate_disabled"
        if self.wear_state is None:
            # No ToF wired into detector → allow (bring-up / unit tests)
            return True, False, 0.0, "no_wear_state"
        lookback = getattr(L, "WEAR_GATE_LOOKBACK_S", 3.0)
        min_frac = getattr(L, "WEAR_GATE_MIN_ON_FRACTION", 0.5)
        g = self.wear_state.gate_fall(
            onset_t, lookback_s=lookback, min_on_fraction=min_frac
        )
        return g.allow, g.helmet_worn_at_onset, g.on_fraction, g.reason

    def _emit_fall(self, now: float, confidence: str) -> Optional[Detection]:
        min_peak = getattr(L, "FALL_MIN_PEAK_G", 5.0)
        if self._peak < min_peak:
            self._reset_state()
            return None
        onset = self._ff_start if self._ff_start is not None else now
        allow, worn_at, frac, reason = self._wear_gate(onset)
        lookback = getattr(L, "WEAR_GATE_LOOKBACK_S", 3.0)
        if not allow:
            self.suppressed_falls += 1
            det = Detection(
                type="fall",
                peak_accel_g=self._peak,
                freefall_ms=self._ff_ms,
                confidence=confidence,
                imu_trace=self._trace_relative(now),
                helmet_worn_at_onset=worn_at,
                helmet_worn_lookback_s=lookback,
                wear_on_fraction=frac,
                suppressed=True,
                suppress_reason=reason,
            )
            self._cooldown_until = now + L.IMU_COOLDOWN_S
            self._reset_state()
            return det
        det = Detection(
            type="fall",
            peak_accel_g=self._peak,
            freefall_ms=self._ff_ms,
            confidence=confidence,
            imu_trace=self._trace_relative(now),
            helmet_worn_at_onset=worn_at,
            helmet_worn_lookback_s=lookback,
            wear_on_fraction=frac,
        )
        self._cooldown_until = now + L.IMU_COOLDOWN_S
        self._reset_state()
        return det

    def _reset_state(self) -> None:
        self._state = "idle"
        self._ff_start = None
        self._ff_ms = 0.0
        self._impact_t = None
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
