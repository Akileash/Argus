"""Hat-on / hat-off detector from the crown VL53L0X (spec §4.3). Pure and deterministic.

Policy choices (documented, configurable):
- status "error" samples carry no information: ignored (neither extend nor reset timers).
  Persistent ToF failure is the supervisor's job (STALE_AFTER_MS), not ours.
- range below min_range_mm (~50 mm, VL53L0X near limit) is unreliable. Policy
  `below_min_policy`: "clamp" (default; treat as a valid reading of min_range_mm, the head
  is very close) or "ignore" (drop the sample).
- Before calibration `hat_on` is True (matches DetectorState default; nothing is suppressed).
- Boot per spec §4.3 happens on a table, so a stable target that is far away is the TABLE, not the
  head. The baseline is only accepted inside the head band (`baseline_max_mm`, default 120 mm); a stable
  out-of-band reading never calibrates. If the real hat measures more than that on the head, raise
  `thresholds.hat_state.baseline_max_mm` (docs/CONNECT-THE-HAT.md).
- Detection t_ns is the confirmation time (monotonic order for the runtime); the moment the
  ToF change began is in debug["off_start_ns"].

cfg keys (optional): baseline_mm (preset, skips calibration), baseline_max_mm, min_range_mm, max_range_mm,
calibration_s, stable_spread_mm, off_delta_mm, on_band_mm, off_confirm_s, on_confirm_s,
knock_lookback_s, knock_slack_ms, below_min_policy.
"""

from __future__ import annotations

import statistics

from argus.contracts import EnvSample, ImuSample, RangeSample, TofSample
from argus.detectors.base import Detection, DetectorState

_S = 1_000_000_000


class HatStateDetector:
    def __init__(self, cfg: dict | None = None, impact_source=None):
        c = cfg or {}
        self.min_range = float(c.get("min_range_mm", 50.0))
        self.max_range = float(c.get("max_range_mm", 1200.0))
        self.baseline_max = float(c.get("baseline_max_mm", 120.0))
        self.calibration_ns = int(float(c.get("calibration_s", 3.0)) * _S)
        self.spread = float(c.get("stable_spread_mm", 10.0))
        self.off_delta = float(c.get("off_delta_mm", 30.0))
        self.on_band = float(c.get("on_band_mm", 15.0))
        self.off_confirm_ns = int(float(c.get("off_confirm_s", 3.0)) * _S)
        self.on_confirm_ns = int(float(c.get("on_confirm_s", 2.0)) * _S)
        self.knock_lookback_ns = int(float(c.get("knock_lookback_s", 2.0)) * _S)
        self.knock_slack_ns = int(float(c.get("knock_slack_ms", 100)) * 1_000_000)
        self.below_min_policy = c.get("below_min_policy", "clamp")
        self.impact_source = impact_source

        self.baseline_mm: float | None = None
        self.calibrated = False
        self._hat_on = True
        self._buf: list[tuple[int, float]] = []
        self._off_start: int | None = None
        self._on_start: int | None = None
        self._own_markers: list[int] = []  # fallback IMU tracking (>3 g) if no impact_source

        if c.get("baseline_mm") is not None:
            self.baseline_mm = float(c["baseline_mm"])
            self.calibrated = True

    @property
    def hat_on(self) -> bool:
        return self._hat_on

    # ------------------------------------------------------------ impact lookup
    def _impact_before(self, t_ns: int) -> bool:
        t = t_ns + self.knock_slack_ns
        lb = self.knock_lookback_ns + self.knock_slack_ns
        if self.impact_source is not None:
            return bool(self.impact_source.recent_impact(t, lb))
        return any(t - lb <= m <= t for m in self._own_markers)

    def on_imu(self, s: ImuSample, state: DetectorState) -> list[Detection]:
        if self.impact_source is None and (s.ax * s.ax + s.ay * s.ay + s.az * s.az) ** 0.5 > 3.0:
            self._own_markers.append(s.t_ns)
            self._own_markers = [m for m in self._own_markers if s.t_ns - m < 30 * _S]
        return []

    # ------------------------------------------------------------ ToF
    def on_tof(self, s: TofSample, state: DetectorState) -> list[Detection]:
        if s.status == "error":
            return []
        r = s.range_mm
        has_target = s.status == "ok" and r is not None
        if has_target and r < self.min_range:
            if self.below_min_policy == "ignore":
                return []
            r = self.min_range
        if has_target and r > self.max_range:
            has_target = False

        val = r if has_target else None
        if not self.calibrated:
            self._calibrate(s.t_ns, val)
            return []
        if self._hat_on:
            return self._track_on(s.t_ns, val)
        return self._track_off(s.t_ns, val)

    def _calibrate(self, t: int, r: float | None) -> None:
        if r is None or r > self.baseline_max:  # no target, or the table (outside the head band)
            self._buf.clear()
            return
        self._buf.append((t, r))
        while self._buf and max(v for _, v in self._buf) - min(v for _, v in self._buf) > self.spread:
            self._buf.pop(0)
        if self._buf and t - self._buf[0][0] >= self.calibration_ns:
            self.baseline_mm = statistics.median(v for _, v in self._buf)
            self.calibrated = True
            self._hat_on = True
            self._buf.clear()

    def _track_on(self, t: int, r: float | None) -> list[Detection]:
        assert self.baseline_mm is not None
        is_off = r is None or r > self.baseline_mm + self.off_delta
        if not is_off:
            self._off_start = None
            return []
        if self._off_start is None:
            self._off_start = t
        if t - self._off_start < self.off_confirm_ns:
            return []
        start = self._off_start
        self._off_start = None
        self._hat_on = False
        self._on_start = None
        knocked = self._impact_before(start)
        return [
            Detection(
                t_ns=t,
                type="hat_knocked_off" if knocked else "hat_removed",
                debug={"off_start_ns": start, "baseline_mm": self.baseline_mm, "range_mm": r},
            )
        ]

    def _track_off(self, t: int, r: float | None) -> list[Detection]:
        assert self.baseline_mm is not None
        near = r is not None and abs(r - self.baseline_mm) <= self.on_band
        if not near:
            self._on_start = None
            return []
        if self._on_start is None:
            self._on_start = t
        if t - self._on_start < self.on_confirm_ns:
            return []
        start = self._on_start
        self._on_start = None
        self._hat_on = True
        self._off_start = None
        return [Detection(t_ns=t, type="hat_on", debug={"on_start_ns": start, "baseline_mm": self.baseline_mm})]

    def on_range(self, s: RangeSample, state: DetectorState) -> list[Detection]:
        return []

    def on_env(self, s: EnvSample, state: DetectorState) -> list[Detection]:
        return []
