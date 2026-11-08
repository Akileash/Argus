"""HC-SR04 driver (gpiozero + lgpio busy-wait) and approach detector."""

from __future__ import annotations

import statistics
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional, Tuple

from gpiozero import DigitalInputDevice, DigitalOutputDevice

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


class ApproachDetector:
    """
    Robust approach detector.

    Fixes the old bug: a dropout then a new echo looked like 10–25 m/s
    "closing" and either false-triggered or poisoned the track.

    Rules:
      - Only compute speed across *continuous* valid samples (short gaps OK).
      - Long gap → break track (clear speed history).
      - Reject physically impossible jumps.
      - Require several agreeing closing estimates before buzzing.
      - Hand-wave / walk-up friendly thresholds in config.py.
    """

    def __init__(self, side: str):
        self.side = side
        self._raw_win: Deque[float] = deque(maxlen=cfg.US_MEDIAN_N)
        self._hist: Deque[Tuple[float, float]] = deque(maxlen=cfg.US_SPEED_WINDOW)
        self._closing_votes: Deque[float] = deque(maxlen=cfg.US_CLOSING_VOTES)
        self._last_good_t: Optional[float] = None
        self._cooldown_until = 0.0
        self.last_distance: Optional[float] = None
        self.last_closing: float = 0.0
        self.track_ok: bool = False

    def update(self, t: float, raw: Optional[float]) -> Optional[ApproachEvent]:
        if raw is None:
            # Soft miss: keep last distance briefly, but do NOT invent speed.
            if (
                self._last_good_t is not None
                and (t - self._last_good_t) > cfg.US_TRACK_BREAK_S
            ):
                self._break_track()
            return None

        # Hard reject after a long gap — re-acquire cleanly
        if (
            self._last_good_t is not None
            and (t - self._last_good_t) > cfg.US_TRACK_BREAK_S
        ):
            self._break_track()

        # Reject teleport jumps BEFORE they enter the median window
        if self._hist:
            t_prev, d_prev = self._hist[-1]
            dt_prev = t - t_prev
            if dt_prev > 0:
                jump_speed = abs(d_prev - raw) / dt_prev
                if jump_speed > cfg.US_MAX_PLAUSIBLE_MPS:
                    return None

        self._raw_win.append(raw)
        filtered = statistics.median(self._raw_win)
        self.last_distance = filtered
        self._last_good_t = t
        self.track_ok = True

        self._hist.append((t, filtered))
        closing = self._estimate_closing()
        if closing is not None:
            self.last_closing = closing
            self._closing_votes.append(closing)

        if t < self._cooldown_until:
            return None
        if len(self._hist) < cfg.US_MIN_SAMPLES_TO_FIRE:
            return None

        # Prefer latest closing; fall back to median of votes if we have several
        if closing is not None:
            closing_use = closing
        elif self._closing_votes:
            closing_use = statistics.median(self._closing_votes)
        else:
            return None
        self.last_closing = closing_use

        if len(self._closing_votes) < cfg.US_CLOSING_VOTES:
            # Still allow instant_drop on a single big step
            if len(self._hist) >= 2:
                _, d_old = self._hist[0]
                drop = d_old - filtered
                if (
                    filtered <= cfg.US_WARN_DISTANCE_M
                    and drop >= cfg.US_INSTANT_DROP_M
                    and closing_use > 0.0
                ):
                    self._cooldown_until = t + cfg.US_COOLDOWN_S
                    self._closing_votes.clear()
                    return ApproachEvent(
                        side=self.side,
                        distance_m=round(filtered, 3),
                        closing_speed_mps=round(closing_use, 2),
                        reason="instant_drop",
                    )
            return None

        reason = self._should_fire(filtered, closing_use)
        if reason is None:
            return None

        self._cooldown_until = t + cfg.US_COOLDOWN_S
        self._closing_votes.clear()
        return ApproachEvent(
            side=self.side,
            distance_m=round(filtered, 3),
            closing_speed_mps=round(closing_use, 2),
            reason=reason,
        )

    def _estimate_closing(self) -> Optional[float]:
        if len(self._hist) < 2:
            return None
        t_old, d_old = self._hist[0]
        t_new, d_new = self._hist[-1]
        dt = t_new - t_old
        if dt < cfg.US_MIN_SPEED_DT_S:
            return None
        return (d_old - d_new) / dt  # +ve = approaching

    def _should_fire(self, distance: float, closing: float) -> Optional[str]:
        # Path A: in warn zone + closing (hand / walk / throw)
        if distance <= cfg.US_WARN_DISTANCE_M and closing >= cfg.US_WARN_CLOSING_MPS:
            return "close_and_fast"

        # Path B: very near and still closing
        if distance <= cfg.US_NEAR_DISTANCE_M and closing >= cfg.US_NEAR_CLOSING_MPS:
            return "very_near"

        # Path C: instant — big drop between consecutive pings (thrown object)
        if len(self._hist) >= 2:
            _, d_old = self._hist[0]
            drop = d_old - distance
            if (
                distance <= cfg.US_WARN_DISTANCE_M
                and drop >= cfg.US_INSTANT_DROP_M
                and closing > 0.0
            ):
                return "instant_drop"

        return None

    def _break_track(self) -> None:
        self._raw_win.clear()
        self._hist.clear()
        self._closing_votes.clear()
        self._last_good_t = None
        self.last_distance = None
        self.last_closing = 0.0
        self.track_ok = False
