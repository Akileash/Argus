"""FAKE teammate imu script (tests only). A blocking ``while True: print(json)`` in a made-up style:
m/s^2 and rad/s, their own axes (their x = hat right, their y = hat forward, z up), nested JSON.

Synthetic data with known hat-frame truth: accel (0.02, -0.03, 1.00) g, gyro (1.0, -2.0, 0.5) deg/s.
``--truth FILE`` instead plays a JSONL of hat-frame samples ({"t_s", "ax".."gz"}) in real time.
Failure drills: ``--crash-after N`` (exit non-zero after N lines), ``--garbage`` (print junk).
"""
import argparse
import json
import math
import sys
import time

G = 9.80665
HAT_ACC, HAT_GYR = (0.02, -0.03, 1.00), (1.0, -2.0, 0.5)


def their_frame(v):
    """hat (x fwd, y left, z up) -> theirs (x right, y fwd, z up)."""
    return -v[1], v[0], v[2]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hz", type=float, default=500.0)
    ap.add_argument("--truth")
    ap.add_argument("--crash-after", type=int, default=0)
    ap.add_argument("--garbage", action="store_true")
    a = ap.parse_args()
    truth = [json.loads(x) for x in open(a.truth)] if a.truth else None
    t0 = time.monotonic()
    n = 0
    while True:
        n += 1
        if a.garbage:
            print("!!! sensor on fire ~~~ %d" % n, flush=True)
            time.sleep(0.01)
            continue
        if truth:
            now = time.monotonic() - t0
            row = next((r for r in truth if r["t_s"] >= now), truth[-1])
            acc, gyr = (row["ax"], row["ay"], row["az"]), (row["gx"], row["gy"], row["gz"])
        else:
            acc, gyr = HAT_ACC, HAT_GYR
        ax, ay, az = (v * G for v in their_frame(acc))
        gx, gy, gz = (math.radians(v) for v in their_frame(gyr))
        print(json.dumps({"ts": time.time(), "accel": {"x": ax, "y": ay, "z": az}, "gyro_rads": [gx, gy, gz]}),
              flush=True)
        if a.crash_after and n >= a.crash_after:
            print("Traceback: IMU bus error", file=sys.stderr)
            sys.exit(3)
        time.sleep(max(0.0, t0 + n / a.hz - time.monotonic()))


main()
