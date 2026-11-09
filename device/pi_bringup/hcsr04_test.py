#!/usr/bin/env python3
"""
HC-SR04 distance / closing-speed sanity check.

Matches the breadboard schematic:
  VCC  → 5V     pin 2
  GND  → GND    pin 6
  TRIG → GPIO23 pin 16
  ECHO → 1 kΩ → junction → GPIO24 pin 18
         junction → 2 kΩ → GND   (5V → ~3.3V divider)

Uses RPi.GPIO with polling (no edge-detect callbacks) so it works on
current Raspberry Pi OS kernels.

Usage:
  cd ~/argus && source .venv/bin/activate
  python hcsr04_test.py              # live distance
  python hcsr04_test.py --approach   # also print closing speed
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import deque

import RPi.GPIO as GPIO

TRIG_GPIO = 23   # physical pin 16
ECHO_GPIO = 24   # physical pin 18
SOUND_MPS = 343.0
MAX_RANGE_M = 4.0
RISE_TIMEOUT_S = 0.02   # wait for Echo to go high after Trig
FALL_TIMEOUT_S = 0.05   # HC-SR04 max pulse ~38 ms when nothing reflects
SETTLE_S = 0.06         # HC-SR04 needs ~60 ms between pings
MEDIAN_N = 5


def ping() -> tuple[float | None, str]:
    """One HC-SR04 measurement. Returns (metres|None, status)."""
    GPIO.output(TRIG_GPIO, False)
    time.sleep(0.000002)
    GPIO.output(TRIG_GPIO, True)
    time.sleep(0.00001)  # 10 µs trigger pulse
    GPIO.output(TRIG_GPIO, False)

    t0 = time.perf_counter()
    while GPIO.input(ECHO_GPIO) == 0:
        if time.perf_counter() - t0 > RISE_TIMEOUT_S:
            return None, "no_echo_rise"
    rise = time.perf_counter()

    while GPIO.input(ECHO_GPIO) == 1:
        if time.perf_counter() - rise > FALL_TIMEOUT_S:
            # No reflector → sensor holds Echo high for ~38 ms
            return None, "out_of_range"
    fall = time.perf_counter()

    dist = (fall - rise) * SOUND_MPS / 2.0
    if dist <= 0.02:
        return None, "too_close"
    if dist > MAX_RANGE_M:
        return None, "out_of_range"
    return dist, "ok"


def main() -> int:
    parser = argparse.ArgumentParser(description="Test HC-SR04 on GPIO23/24")
    parser.add_argument(
        "--approach",
        action="store_true",
        help="Print closing speed (m/s) from successive median distances",
    )
    parser.add_argument(
        "--hz",
        type=float,
        default=15.0,
        help="Ping rate (default 15 Hz; sensor needs ~60 ms settle)",
    )
    args = parser.parse_args()

    # Don't ping faster than the sensor can settle
    period = max(1.0 / max(args.hz, 1.0), SETTLE_S)
    window: deque[float] = deque(maxlen=MEDIAN_N)
    last_med: float | None = None
    last_t: float | None = None

    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(TRIG_GPIO, GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(ECHO_GPIO, GPIO.IN)

    # Brief settle so first ping isn't garbage after power-up
    time.sleep(0.1)
    echo_idle = GPIO.input(ECHO_GPIO)
    print(
        f"HC-SR04 test  TRIG=GPIO{TRIG_GPIO} (pin 16)  "
        f"ECHO=GPIO{ECHO_GPIO} (pin 18)  period={period*1000:.0f} ms"
    )
    print(f"ECHO idle level = {echo_idle} (expect 0). Hold a box ~0.3–1.5 m in front.")
    print("Ctrl+C to stop.\n")
    if args.approach:
        print(f"{'raw_m':>8} {'med_m':>8} {'close_mps':>10}  status")
    else:
        print(f"{'raw_m':>8} {'med_m':>8}  status")

    try:
        while True:
            t = time.perf_counter()
            d, status = ping()
            if d is not None:
                window.append(d)
                med = statistics.median(window)
                if args.approach and last_med is not None and last_t is not None:
                    dt = t - last_t
                    closing = (last_med - med) / dt if dt > 0 else 0.0
                    print(
                        f"{d:8.3f} {med:8.3f} {closing:10.2f}  {status}",
                        flush=True,
                    )
                else:
                    print(f"{d:8.3f} {med:8.3f}  {status}", flush=True)
                last_med, last_t = med, t
            else:
                print(f"{'----':>8} {'----':>8}  {status}", flush=True)

            elapsed = time.perf_counter() - t
            time.sleep(max(0.0, period - elapsed))
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    finally:
        GPIO.cleanup((TRIG_GPIO, ECHO_GPIO))


if __name__ == "__main__":
    raise SystemExit(main())
