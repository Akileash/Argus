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
        self._history_s = history_s
        self.current_worn = False
        self.last_distance_mm: Optional[int] = None
        self.last_near = False
        self.updates = 0
        self.ready = False  # True after at least one ToF update

    def update(
        self,
        worn: bool,
        *,
        t: Optional[float] = None,
        distance_mm: Optional[int] = None,
        near: bool = False,
    ) -> None:
        now = time.perf_counter() if t is None else t
        with self._lock:
            self.current_worn = worn
            self.last_distance_mm = distance_mm
            self.last_near = near
            self.updates += 1
            self.ready = True
            self._hist.append((now, worn))
            cutoff = now - self._history_s
            while self._hist and self._hist[0][0] < cutoff:
                self._hist.popleft()

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
