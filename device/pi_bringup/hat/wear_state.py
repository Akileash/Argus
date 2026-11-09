"""Thread-safe helmet wear history for IMU fall gating.

ToF owns the wear truth. IMU fall detection may only emit `fall` when
the helmet was ON beforehand (lookback window).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional, Tuple


@dataclass
class WearGateResult:
    allow: bool
    helmet_worn_at_onset: bool
    helmet_worn_lookback_s: float
    on_fraction: float
    reason: str


class WearState:
    """Ring of (t, worn) samples. Updated by the ToF wear thread."""

    def __init__(self, history_s: float = 8.0) -> None:
        self._lock = threading.Lock()
        self._hist: Deque[Tuple[float, bool]] = deque()
        # Raw (t, near) per ToF poll, NOT dwell-filtered (knock-off detection).
        self._near_hist: Deque[Tuple[float, bool]] = deque()
        self._history_s = history_s
        self.current_worn = False
        self.last_distance_mm: Optional[int] = None
        self.last_near = False
        self.updates = 0
        self.ready = False  # True after at least one ToF update
        self._last_impact_t: Optional[float] = None

    def note_impact(self, t: float) -> None:
        """IMU saw a hard hit (same perf_counter clock as the ToF polls)."""
        self._last_impact_t = t

    def impact_within(self, now: float, seconds: float) -> bool:
        t = self._last_impact_t
        return t is not None and 0.0 <= now - t <= seconds

    def update(
        self,
        worn: bool,
        *,
        t: Optional[float] = None,
        distance_mm: Optional[int] = None,
        near: Optional[bool] = None,
    ) -> None:
        now = time.perf_counter() if t is None else t
        with self._lock:
            self.current_worn = worn
            self.last_distance_mm = distance_mm
            self.last_near = bool(near)
            self.updates += 1
            self.ready = True
            self._hist.append((now, worn))
            cutoff = now - self._history_s
            while self._hist and self._hist[0][0] < cutoff:
                self._hist.popleft()
            if near is not None:
                self._near_hist.append((now, bool(near)))
                while self._near_hist and self._near_hist[0][0] < cutoff:
                    self._near_hist.popleft()

    def not_near_run_start(
        self,
        now: float,
        *,
        since_t: float,
        min_run_s: float,
        max_stale_s: float,
        min_samples: int = 4,
    ) -> Optional[float]:
        """Start time of the current unbroken run of raw not-near ToF polls.

        Returns None unless the run (a) began at/after `since_t`, (b) lasted at
        least `min_run_s` up to the newest poll, (c) holds >= min_samples
        polls, (d) the newest poll is fresher than `max_stale_s` (else the ToF
        thread is stalled/unavailable and we know nothing), and (e) was
        preceded by a near poll (the hat really was on the head). Any single
        near poll breaks the run, so brief dropouts on a hat that stays on do
        not count. Invalid readings are not distinguishable from "far" here:
        the VL6180X reports free air as no-target/invalid.
        """
        with self._lock:
            h = self._near_hist
            if not h or now - h[-1][0] > max_stale_s or h[-1][1]:
                return None
            i = len(h) - 1
            while i >= 0 and not h[i][1]:
                i -= 1
            if i < 0:
                return None  # never saw it near in the retained history
            run = list(h)[i + 1:]
            if len(run) < min_samples:
                return None
            start = run[0][0]
            if start < since_t or run[-1][0] - start < min_run_s:
                return None
            return start

    def _state_at_locked(self, t_onset: float) -> bool:
        """Last known worn flag at or before t_onset."""
        state = False
        for t, worn in self._hist:
            if t > t_onset:
                break
            state = worn
        return state

    def gate_fall(
        self,
        t_onset: float,
        *,
        lookback_s: float,
        min_on_fraction: float = 0.5,
    ) -> WearGateResult:
        """
        Allow a fall only if the helmet was worn beforehand.

        Rules:
          - Need ToF history (ready).
          - State at freefall onset must be worn.
          - In [t_onset - lookback_s, t_onset], fraction of time worn
            must be >= min_on_fraction (default 50%).
        """
        with self._lock:
            if not self.ready or not self._hist:
                return WearGateResult(
                    allow=False,
                    helmet_worn_at_onset=False,
                    helmet_worn_lookback_s=lookback_s,
                    on_fraction=0.0,
                    reason="no_tof_history",
                )

            worn_at = self._state_at_locked(t_onset)
            t0 = t_onset - lookback_s
            # Build piecewise-constant coverage from samples in/near window
            samples = [(t, w) for t, w in self._hist if t <= t_onset]
            if not samples:
                return WearGateResult(
                    allow=False,
                    helmet_worn_at_onset=False,
                    helmet_worn_lookback_s=lookback_s,
                    on_fraction=0.0,
                    reason="no_samples_before_onset",
                )

            # Extend with a synthetic point at window start using earliest
            # known state before t0 (or first sample).
            prev_worn = samples[0][1]
            for t, w in samples:
                if t < t0:
                    prev_worn = w
                else:
                    break

            points = [(t0, prev_worn)]
            for t, w in samples:
                if t0 <= t <= t_onset:
                    points.append((t, w))
            if points[-1][0] < t_onset:
                points.append((t_onset, points[-1][1]))

            on_s = 0.0
            for i in range(len(points) - 1):
                dt = points[i + 1][0] - points[i][0]
                if dt > 0 and points[i][1]:
                    on_s += dt
            frac = on_s / lookback_s if lookback_s > 0 else 0.0

            if not worn_at:
                return WearGateResult(
                    allow=False,
                    helmet_worn_at_onset=False,
                    helmet_worn_lookback_s=lookback_s,
                    on_fraction=frac,
                    reason="helmet_off_at_onset",
                )
            if frac < min_on_fraction:
                return WearGateResult(
                    allow=False,
                    helmet_worn_at_onset=True,
                    helmet_worn_lookback_s=lookback_s,
                    on_fraction=frac,
                    reason="helmet_off_lookback",
                )
            return WearGateResult(
                allow=True,
                helmet_worn_at_onset=True,
                helmet_worn_lookback_s=lookback_s,
                on_fraction=frac,
                reason="ok",
            )
