#!/usr/bin/env python3
"""
Line-of-fire tracker for HC-SR04 (front of hat).

HC-SR04 often drops soft targets (people / clothing) when they stand still —
returns only show up while they move. This tracker:

  1. Holds last-known distance through brief dropouts (presence memory)
  2. Estimates closing speed from the valid hits we do get
  3. Estimates approach acceleration (closing speed increasing)
  4. Fires a TRIGGER when something is close AND closing fast

Wiring (same as schematic):
  VCC  → 5V     pin 2
  GND  → GND    pin 6
  TRIG → GPIO23 pin 16
  ECHO → 1 kΩ → junction → GPIO24 pin 18 ; junction → 2 kΩ → GND

Usage:
  cd ~/argus && source .venv/bin/activate
  python hcsr04_lof.py
  python hcsr04_lof.py --demo-object   # tighter thresholds for a thrown box
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Deque, Optional, Tuple

import RPi.GPIO as GPIO

# ---------------------------------------------------------------------------
# Pins / physics
# ---------------------------------------------------------------------------
TRIG_GPIO = 23
ECHO_GPIO = 24
SOUND_MPS = 343.0
MAX_RANGE_M = 3.5          # ignore beyond this (demo cone)
MIN_RANGE_M = 0.05
RISE_TIMEOUT_S = 0.02
FALL_TIMEOUT_S = 0.05
SETTLE_S = 0.06
SAMPLE_HZ = 15.0

# ---------------------------------------------------------------------------
# Presence / motion (tune freely)
# ---------------------------------------------------------------------------
MEDIAN_N = 5
# Keep showing an object for this long after the last good echo
PRESENCE_HOLD_S = 0.80
# Forget speed history if we lose the track longer than this
TRACK_BREAK_S = 1.20
# Closing-speed estimate needs at least this much time between anchors
MIN_SPEED_DT_S = 0.08
MAX_SPEED_DT_S = 0.60
# Reject impossible ultrasonic jumps (noise)
MAX_JUMP_MPS = 12.0

# Line-of-fire trigger (person / thrown object)
WARN_DISTANCE_M = 1.50
WARN_CLOSING_MPS = 1.20
WARN_ACCEL_MPS2 = 2.0      # optional boost path
COOLDOWN_S = 2.5

# Stricter path: very close even if slower
NEAR_DISTANCE_M = 0.60
NEAR_CLOSING_MPS = 0.40

EVENTS_PATH = Path(__file__).resolve().parent / "events.jsonl"
HAT_ID = "hat-01"


# =============================================================================
# Hardware ping
# =============================================================================

def ping() -> Tuple[Optional[float], str]:
    GPIO.output(TRIG_GPIO, False)
    time.sleep(0.000002)
    GPIO.output(TRIG_GPIO, True)
    time.sleep(0.00001)
    GPIO.output(TRIG_GPIO, False)

    t0 = time.perf_counter()
    while GPIO.input(ECHO_GPIO) == 0:
        if time.perf_counter() - t0 > RISE_TIMEOUT_S:
            return None, "no_echo_rise"
    rise = time.perf_counter()

    while GPIO.input(ECHO_GPIO) == 1:
        if time.perf_counter() - rise > FALL_TIMEOUT_S:
            return None, "out_of_range"
    fall = time.perf_counter()

    dist = (fall - rise) * SOUND_MPS / 2.0
    if dist < MIN_RANGE_M:
        return None, "too_close"
    if dist > MAX_RANGE_M:
        return None, "out_of_range"
    return dist, "ok"


# =============================================================================
# Tracker
# =============================================================================

@dataclass
class TrackState:
    present: bool = False
    distance_m: Optional[float] = None
    closing_mps: float = 0.0
    accel_mps2: float = 0.0
    hit_rate: float = 0.0          # fraction of recent pings that returned
    raw_status: str = "init"
    triggered: bool = False
    reason: str = ""


class LineOfFireTracker:
    """Presence-holding + closing-speed / accel estimator."""

    def __init__(self) -> None:
        self._raw_win: Deque[float] = deque(maxlen=MEDIAN_N)
        self._hit_flags: Deque[int] = deque(maxlen=20)
        self._last_good_t: Optional[float] = None
        self._last_good_d: Optional[float] = None
        self._anchor_t: Optional[float] = None
        self._anchor_d: Optional[float] = None
        self._closing: float = 0.0
        self._prev_closing: float = 0.0
        self._prev_closing_t: Optional[float] = None
        self._accel: float = 0.0
        self._cooldown_until: float = 0.0

    def update(self, t: float, raw: Optional[float], status: str) -> TrackState:
        self._hit_flags.append(1 if raw is not None else 0)
        hit_rate = sum(self._hit_flags) / max(1, len(self._hit_flags))

        if raw is not None:
            self._raw_win.append(raw)
            med = statistics.median(self._raw_win)
            self._last_good_t = t
            self._last_good_d = med
            self._update_motion(t, med)
        else:
            # Soft dropout: keep last distance for PRESENCE_HOLD_S
            if (
                self._last_good_t is not None
                and (t - self._last_good_t) > TRACK_BREAK_S
            ):
                self._break_track()

        present = (
            self._last_good_t is not None
            and (t - self._last_good_t) <= PRESENCE_HOLD_S
            and self._last_good_d is not None
        )
        distance = self._last_good_d if present else None
        if not present:
            # Decay motion estimates when nothing is there
            self._closing *= 0.5
            self._accel *= 0.5
            if abs(self._closing) < 0.05:
                self._closing = 0.0
            if abs(self._accel) < 0.1:
                self._accel = 0.0

        triggered, reason = self._should_trigger(t, present, distance)

        return TrackState(
            present=present,
            distance_m=distance,
            closing_mps=self._closing,
            accel_mps2=self._accel,
            hit_rate=hit_rate,
            raw_status=status if raw is None else "ok",
            triggered=triggered,
            reason=reason,
        )

    def _update_motion(self, t: float, med: float) -> None:
        if self._anchor_t is None or self._anchor_d is None:
            self._anchor_t = t
            self._anchor_d = med
            return

        dt = t - self._anchor_t
        if dt < MIN_SPEED_DT_S:
            return
        if dt > MAX_SPEED_DT_S:
            # Stale anchor after a gap — reset without inventing speed
            self._anchor_t = t
            self._anchor_d = med
            return

        closing = (self._anchor_d - med) / dt  # +ve = approaching
        if abs(closing) > MAX_JUMP_MPS:
            # Spike — ignore and re-anchor
            self._anchor_t = t
            self._anchor_d = med
            return

        # Light EMA so single noisy hits don't dominate
        self._closing = 0.55 * closing + 0.45 * self._closing

        if self._prev_closing_t is not None:
            adt = t - self._prev_closing_t
            if adt >= MIN_SPEED_DT_S:
                self._accel = (self._closing - self._prev_closing) / adt
                self._prev_closing = self._closing
                self._prev_closing_t = t
        else:
            self._prev_closing = self._closing
            self._prev_closing_t = t

        self._anchor_t = t
        self._anchor_d = med

    def _break_track(self) -> None:
        self._raw_win.clear()
        self._last_good_t = None
        self._last_good_d = None
        self._anchor_t = None
        self._anchor_d = None
        self._closing = 0.0
        self._accel = 0.0
        self._prev_closing = 0.0
        self._prev_closing_t = None

    def _should_trigger(
        self,
        t: float,
        present: bool,
        distance: Optional[float],
    ) -> Tuple[bool, str]:
        if t < self._cooldown_until or not present or distance is None:
            return False, ""

        # Path A: classic line-of-fire — close + fast
        if distance < WARN_DISTANCE_M and self._closing >= WARN_CLOSING_MPS:
            self._cooldown_until = t + COOLDOWN_S
            return True, "close_and_fast"

        # Path B: accelerating hard toward the hat while already in zone
        if (
            distance < WARN_DISTANCE_M
            and self._closing >= 0.6
            and self._accel >= WARN_ACCEL_MPS2
        ):
            self._cooldown_until = t + COOLDOWN_S
            return True, "accelerating"

        # Path C: very near + still closing (creeping into face)
        if distance < NEAR_DISTANCE_M and self._closing >= NEAR_CLOSING_MPS:
            self._cooldown_until = t + COOLDOWN_S
            return True, "very_near"

        return False, ""


def append_event(state: TrackState) -> dict:
    now = datetime.now().astimezone()
    event = {
        "event_id": str(uuid.uuid4()),
        "hat_id": HAT_ID,
        "type": "line_of_fire",
        "timestamp": now.isoformat(timespec="milliseconds"),
        "priority": "high",
        "sensor": {
            "distance_m": round(state.distance_m or 0.0, 3),
            "closing_speed_mps": round(state.closing_mps, 2),
            "accel_mps2": round(state.accel_mps2, 2),
            "hit_rate": round(state.hit_rate, 2),
            "trigger_reason": state.reason,
        },
        "confidence": "high" if state.hit_rate >= 0.3 else "medium",
    }
    with EVENTS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
    return event


def apply_demo_object_thresholds() -> None:
    """Tighter / slower thresholds for a thrown box demo."""
    global WARN_DISTANCE_M, WARN_CLOSING_MPS, NEAR_DISTANCE_M, NEAR_CLOSING_MPS
    WARN_DISTANCE_M = 1.80
    WARN_CLOSING_MPS = 0.80
    NEAR_DISTANCE_M = 0.80
    NEAR_CLOSING_MPS = 0.25


def main() -> int:
    parser = argparse.ArgumentParser(description="HC-SR04 line-of-fire tracker")
    parser.add_argument(
        "--demo-object",
        action="store_true",
        help="Looser thresholds for a firm thrown box / ball",
    )
    parser.add_argument(
        "--log-events",
        action="store_true",
        help=f"Append TRIGGERs to {EVENTS_PATH.name}",
    )
    args = parser.parse_args()
    if args.demo_object:
        apply_demo_object_thresholds()

    period = max(1.0 / SAMPLE_HZ, SETTLE_S)
    tracker = LineOfFireTracker()

    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(TRIG_GPIO, GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(ECHO_GPIO, GPIO.IN)
    time.sleep(0.1)

    print(
        f"Line-of-fire  TRIG=GPIO{TRIG_GPIO} ECHO=GPIO{ECHO_GPIO}  "
        f"warn<{WARN_DISTANCE_M}m & >{WARN_CLOSING_MPS}m/s"
    )
    print(
        "Soft targets (people) drop out when still — presence is HELD "
        f"for {PRESENCE_HOLD_S*1000:.0f} ms after last echo."
    )
    print("Walk / throw toward the sensor. Ctrl+C to stop.\n")
    print(
        f"{'present':>7} {'dist_m':>7} {'close':>7} {'accel':>7} "
        f"{'hits':>5}  raw          note"
    )

    try:
        while True:
            t = time.perf_counter()
            raw, status = ping()
            state = tracker.update(t, raw, status)

            dist_s = f"{state.distance_m:7.2f}" if state.distance_m is not None else f"{'----':>7}"
            note = ""
            if state.triggered:
                note = f"*** TRIGGER ({state.reason}) ***"
                print(
                    f"{'YES' if state.present else 'no':>7} {dist_s} "
                    f"{state.closing_mps:7.2f} {state.accel_mps2:7.2f} "
                    f"{state.hit_rate:5.2f}  {state.raw_status:<12} {note}",
                    flush=True,
                )
                if args.log_events:
                    ev = append_event(state)
                    print(f"EVENT {json.dumps(ev)}", flush=True)
            else:
                # One-line live view
                print(
                    f"{'YES' if state.present else 'no':>7} {dist_s} "
                    f"{state.closing_mps:7.2f} {state.accel_mps2:7.2f} "
                    f"{state.hit_rate:5.2f}  {state.raw_status:<12}",
                    end="\r",
                    flush=True,
                )

            elapsed = time.perf_counter() - t
            time.sleep(max(0.0, period - elapsed))
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    finally:
        GPIO.cleanup((TRIG_GPIO, ECHO_GPIO))


if __name__ == "__main__":
    raise SystemExit(main())
