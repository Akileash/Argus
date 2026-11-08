"""
Ultrasonics + buzzers only (no IMU).

  python3 -m hat.tools.test_us_haptics
  python3 -m hat.tools.test_us_haptics --buzz-only   # motors only
  python3 -m hat.tools.test_us_haptics --sense-only  # distances only
"""

from __future__ import annotations

import argparse
import signal
import sys
import time

from gpiozero import Device
from gpiozero.pins.lgpio import LGPIOFactory

Device.pin_factory = LGPIOFactory()

from hat import config as cfg
from hat.haptics import Haptics
from hat.ultrasonic import ApproachDetector, HCSR04


def _sigterm(_s, _f) -> None:
    raise KeyboardInterrupt


signal.signal(signal.SIGTERM, _sigterm)


def buzz_only() -> int:
    h = Haptics(dry_run=False)
    try:
        print("LEFT motor…")
        h.warn("left")
        time.sleep(1.2)
        print("RIGHT motor…")
        h.warn("right")
        time.sleep(1.2)
        print("Done.")
        return 0
    finally:
        h.close()


def sense_only() -> int:
    left = HCSR04(cfg.US_LEFT_TRIG, cfg.US_LEFT_ECHO, "left")
    right = HCSR04(cfg.US_RIGHT_TRIG, cfg.US_RIGHT_ECHO, "right")
    print(f"{'L_m':>8} {'R_m':>8}   Ctrl+C to stop")
    try:
        while True:
            dl = left.distance_m()
            time.sleep(cfg.US_PING_GAP_S)
            dr = right.distance_m()
            ls = f"{dl:8.3f}" if dl is not None else f"{'----':>8}"
            rs = f"{dr:8.3f}" if dr is not None else f"{'----':>8}"
            print(f"{ls} {rs}", flush=True)
            time.sleep(cfg.US_PING_GAP_S)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    finally:
        left.close()
        right.close()


def sense_and_buzz() -> int:
    """Approach on a side → that side's motor buzzes. No IMU."""
    h = Haptics(dry_run=False)
    left = HCSR04(cfg.US_LEFT_TRIG, cfg.US_LEFT_ECHO, "left")
    right = HCSR04(cfg.US_RIGHT_TRIG, cfg.US_RIGHT_ECHO, "right")
    det_l = ApproachDetector("left")
    det_r = ApproachDetector("right")
    sensors = [(left, det_l), (right, det_r)]
    idx = 0

    print(
        "Ultrasonics + buzzers (no IMU). "
        "Approach LEFT → left buzz, RIGHT → right buzz. Ctrl+C to stop."
    )
    try:
        while True:
            t0 = time.perf_counter()
            sensor, det = sensors[idx]
            idx = 1 - idx
            d = sensor.distance_m()
            ev = det.update(t0, d)
            dl, dr = det_l.last_distance, det_r.last_distance
            ls = f"{dl:5.2f}" if dl is not None else " ----"
            rs = f"{dr:5.2f}" if dr is not None else " ----"
            print(f"\rL={ls}  R={rs}   ", end="", flush=True)
            if ev is not None:
                side = "both" if cfg.HAPTIC_WARN_BOTH else ev.side
                print(
                    f"\n>>> DETECTED  side={ev.side}  "
                    f"distance={ev.distance_m} m  "
                    f"closing={ev.closing_speed_mps} m/s  "
                    f"reason={ev.reason}",
                    flush=True,
                )
                print(f">>> BUZZING   motor={side}  NOW", flush=True)
                t_buzz = time.perf_counter()
                h.warn(side)
                print(
                    f">>> BUZZ OK   motor={side}  "
                    f"(started in {(time.perf_counter() - t_buzz)*1000:.1f} ms)\n",
                    flush=True,
                )
            elapsed = time.perf_counter() - t0
            time.sleep(max(0.0, cfg.US_PING_GAP_S - elapsed))
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    finally:
        h.close()
        left.close()
        right.close()


def main(argv: list | None = None) -> int:
    p = argparse.ArgumentParser(description="Test ultrasonics + buzzers only")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--buzz-only", action="store_true", help="Pulse each motor once")
    g.add_argument("--sense-only", action="store_true", help="Print L/R distances only")
    args = p.parse_args(argv)

    if args.buzz_only:
        return buzz_only()
    if args.sense_only:
        return sense_only()
    return sense_and_buzz()


if __name__ == "__main__":
    raise SystemExit(main())
