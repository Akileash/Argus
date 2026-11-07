"""Fall / head-impact detector (spec §4.2). Pure, time-based (works at 100-1000 Hz input).

NOTE on sample rate: impact pulses last ~3-10 ms. At 1 kHz a 3 ms spike yields 3 samples
and is caught; a 100 Hz stream may have no sample inside the pulse and miss it entirely
(see tests: test_1khz_spike_caught_100hz_misses). Feed full-rate FIFO data here.

cfg keys (all optional, spec defaults): impact_g, saturation_g, free_fall_g,
free_fall_min_ms, landing_g, landing_window_ms, pulse_end_g, pulse_hold_ms,
max_pulse_ms, debounce_ms, ff_gap_tol_ms, gravity_mps2, marker_retention_s.
"""

from __future__ import annotations

import math
from collections import deque

from argus.contracts import EnvSample, ImuSample, RangeSample, TofSample
from argus.detectors.base import Detection, DetectorState

_MS = 1_000_000


class FallImpactDetector:
    def __init__(self, cfg: dict | None = None):
        c = cfg or {}
        self.impact_g = float(c.get("impact_g", 6.0))
        self.saturation_g = float(c.get("saturation_g", 15.9))
        self.free_fall_g = float(c.get("free_fall_g", 0.35))
        self.free_fall_min_ns = int(float(c.get("free_fall_min_ms", 200)) * _MS)
        self.landing_g = float(c.get("landing_g", 3.0))
        self.landing_window_ns = int(float(c.get("landing_window_ms", 1000)) * _MS)
        self.pulse_end_g = float(c.get("pulse_end_g", 3.0))
        self.pulse_hold_ns = int(float(c.get("pulse_hold_ms", 30)) * _MS)
        self.max_pulse_ns = int(float(c.get("max_pulse_ms", 500)) * _MS)
        self.debounce_ns = int(float(c.get("debounce_ms", 300)) * _MS)
        self.ff_gap_ns = int(float(c.get("ff_gap_tol_ms", 15)) * _MS)
        self.g = float(c.get("gravity_mps2", 9.80665))
        self.retention_ns = int(float(c.get("marker_retention_s", 30)) * 1_000_000_000)

        self._ff_start: int | None = None
        self._ff_last = 0
        self._armed: tuple[int, int] | None = None  # (start, last) of a finished qualifying run
        self._pulse: dict | None = None
        self._debounce_until = 0
        self._markers: deque[int] = deque()  # times of >landing_g samples / qualifying free fall

    # ------------------------------------------------------------ public helpers
    def recent_impact(self, t_ns: int, lookback_ns: int) -> bool:
        """True if an impact (>3 g) or qualifying free fall was seen in [t-lookback, t]."""
        lo = t_ns - lookback_ns
        return any(lo <= m <= t_ns for m in self._markers)

    # ------------------------------------------------------------ internals
    def _mark(self, t: int) -> None:
        if not self._markers or t - self._markers[-1] >= 5 * _MS:
            self._markers.append(t)
        while self._markers and t - self._markers[0] > self.retention_ns:
            self._markers.popleft()

    def _finalize(self) -> Detection:
        p = self._pulse
        assert p is not None
        self._pulse = None
        self._debounce_until = p["last_hi"] + self.debounce_ns
        common = {
            "t_ns": p["start"],
            "peak_g": p["peak"],
            "peak_g_saturated": p["sat"],
            "debug": {"pulse_ms": (p["last_hi"] - p["start"]) / _MS},
        }
        if p["kind"] == "fall":
            t = p["ff_ms"] / 1000.0
            return Detection(
                type="fall",
                free_fall_ms=p["ff_ms"],
                drop_height_m=0.5 * self.g * t * t,
                **common,
            )
        return Detection(type="head_impact", **common)

    def _landing_candidate(self, t: int) -> float | None:
        """Free-fall duration (ms) if a qualifying fall precedes a landing at time t."""
        if self._ff_start is not None:
            dur = self._ff_last - self._ff_start
            if dur >= self.free_fall_min_ns and t - self._ff_last <= self.landing_window_ns:
                return dur / _MS
        if self._armed is not None:
            s, last = self._armed
            if t - last <= self.landing_window_ns:
                return (last - s) / _MS
        return None

    # ------------------------------------------------------------ Detector API
    def on_imu(self, s: ImuSample, state: DetectorState) -> list[Detection]:
        t = s.t_ns
        mag = math.sqrt(s.ax * s.ax + s.ay * s.ay + s.az * s.az)
        sat = max(abs(s.ax), abs(s.ay), abs(s.az)) >= self.saturation_g
        out: list[Detection] = []

        if mag > self.landing_g:
            self._mark(t)

        # 1) active pulse bookkeeping
        if self._pulse is not None:
            p = self._pulse
            if mag >= self.pulse_end_g:
                p["last_hi"] = t
                p["peak"] = max(p["peak"], mag)
                p["sat"] = p["sat"] or sat
            if t - p["last_hi"] > self.pulse_hold_ns or t - p["start"] > self.max_pulse_ns:
                out.append(self._finalize())

        # 2) free-fall tracking
        if mag < self.free_fall_g:
            if self._ff_start is None or t - self._ff_last > self.ff_gap_ns:
                if self._ff_start is not None and self._ff_last - self._ff_start >= self.free_fall_min_ns:
                    self._armed = (self._ff_start, self._ff_last)
                self._ff_start = t
            self._ff_last = t
            if t - self._ff_start >= self.free_fall_min_ns:
                self._mark(t)

        # 3) pulse start
        if self._pulse is None and t >= self._debounce_until and mag >= self.landing_g:
            ff_ms = self._landing_candidate(t)
            if ff_ms is not None:
                self._pulse = {"start": t, "last_hi": t, "peak": mag, "sat": sat, "kind": "fall", "ff_ms": ff_ms}
                self._ff_start = None
                self._armed = None
            elif mag >= self.impact_g:
                self._pulse = {"start": t, "last_hi": t, "peak": mag, "sat": sat, "kind": "impact", "ff_ms": 0.0}
        return out

    def on_range(self, s: RangeSample, state: DetectorState) -> list[Detection]:
        return []

    def on_tof(self, s: TofSample, state: DetectorState) -> list[Detection]:
        return []

    def on_env(self, s: EnvSample, state: DetectorState) -> list[Detection]:
        return []
