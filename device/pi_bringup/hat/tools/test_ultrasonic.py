"""
Ultrasonic test tool (LEFT + RIGHT HC-SR04).

  python3 -m hat.tools.test_ultrasonic live
  python3 -m hat.tools.test_ultrasonic rate
  python3 -m hat.tools.test_ultrasonic detect [--log path.csv]
"""

from __future__ import annotations

import argparse
import csv
import signal
import sys
import time
from pathlib import Path

def _sigterm(_signum, _frame) -> None:
    raise KeyboardInterrupt

signal.signal(signal.SIGTERM, _sigterm)

from gpiozero import Device
from gpiozero.pins.lgpio import LGPIOFactory

Device.pin_factory = LGPIOFactory()

from hat import config as cfg
from hat.events import build_event, emit
from hat.ultrasonic import ApproachDetector, HCSR04


def _open_pair():
    left = HCSR04(cfg.US_LEFT_TRIG, cfg.US_LEFT_ECHO, name="left")
    right = HCSR04(cfg.US_RIGHT_TRIG, cfg.US_RIGHT_ECHO, name="right")
    return left, right


def cmd_live() -> int:
    left, right = _open_pair()
    try:
        print("Alternating L/R. Wave a hand / box behind each sensor. Ctrl+C to stop.\n")
        print(f"{'L_m':>8} {'R_m':>8}")
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


def cmd_rate() -> int:
    left, right = _open_pair()
    seconds = 3.0
    hits = {"left": 0, "right": 0}
    tries = {"left": 0, "right": 0}
    t0 = time.perf_counter()
    side = "left"
    try:
        while time.perf_counter() - t0 < seconds:
            sensor = left if side == "left" else right
            tries[side] += 1
            if sensor.distance_m() is not None:
                hits[side] += 1
            side = "right" if side == "left" else "left"
            time.sleep(cfg.US_PING_GAP_S)
        elapsed = time.perf_counter() - t0
        for s in ("left", "right"):
            hz = tries[s] / elapsed
            rate = hits[s] / max(tries[s], 1)
            print(
                f"{s}: {tries[s]} pings ({hz:.1f} Hz), "
                f"echo hit-rate={rate:.0%} ({hits[s]}/{tries[s]})"
            )
        print("(Hit-rate depends on something being in the cone.)")
        return 0
    finally:
        left.close()
        right.close()


def cmd_detect(log_path: str | None) -> int:
    left, right = _open_pair()
    det_l = ApproachDetector("left")
    det_r = ApproachDetector("right")
    writer = None
    fh = None
    if log_path:
        fh = Path(log_path).open("w", newline="", encoding="utf-8")
        writer = csv.writer(fh)
        writer.writerow(["t", "side", "distance_m"])

    print(
        f"Approach detect  warn <{cfg.US_WARN_DISTANCE_M} m "
        f"and >{cfg.US_MIN_OBJ_MPS} m/s (no IMU gating here). Ctrl+C to stop."
    )
    sensors = [(left, det_l, "left"), (right, det_r, "right")]
    idx = 0
    try:
        while True:
            t0 = time.perf_counter()
            sensor, det, side = sensors[idx]
            idx = 1 - idx
            d = sensor.distance_m()
            if writer is not None:
                writer.writerow([f"{t0:.6f}", side, "" if d is None else f"{d:.4f}"])
            ev = det.update(t0, d)
            if ev is not None:
                emit(
                    build_event(
                        "line_of_fire",
                        side=ev.side,
                        distance_m=ev.distance_m,
                        closing_speed_mps=ev.closing_speed_mps,
                    )
                )
            else:
                ls = det_l.last_distance
                rs = det_r.last_distance
                print(
                    f"L={ls if ls is not None else float('nan'):5.2f}  "
                    f"R={rs if rs is not None else float('nan'):5.2f}  "
                    f"ping={side}",
                    end="\r",
                    flush=True,
                )
            elapsed = time.perf_counter() - t0
            time.sleep(max(0.0, cfg.US_PING_GAP_S - elapsed))
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    finally:
        left.close()
        right.close()
        if fh:
            fh.close()


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="HC-SR04 dual-sensor tests")
    parser.add_argument("mode", choices=["live", "rate", "detect"])
    parser.add_argument("--log", default=None, help="CSV path for detect mode")
    args = parser.parse_args(argv)

    if args.mode == "live":
        return cmd_live()
    if args.mode == "rate":
        return cmd_rate()
    return cmd_detect(args.log)


if __name__ == "__main__":
    raise SystemExit(main())
