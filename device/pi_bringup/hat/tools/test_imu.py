"""
IMU test tool.

  python3 -m hat.tools.test_imu check
  python3 -m hat.tools.test_imu live
  python3 -m hat.tools.test_imu rate
  python3 -m hat.tools.test_imu detect [--log path.csv]
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

from hat import config as cfg
from hat.events import build_event, emit
from hat.imu import FallImpactDetector, MPU6050


def cmd_check() -> int:
    mpu = MPU6050()
    try:
        who = mpu.who_am_i()
        print(f"WHO_AM_I = 0x{who:02X} (expect 0x68 or 0x70)")
        if who not in (0x68, 0x70):
            print("Unexpected id — check wiring.", file=sys.stderr)
            return 1
        # Resting |a| near 1 g
        time.sleep(0.05)
        mags = []
        for _ in range(50):
            s = mpu.read()
            mags.append(s.mag)
            time.sleep(0.01)
        mean = sum(mags) / len(mags)
        print(f"|a| mean over 0.5 s = {mean:.3f} g  (expect ~1.00 at rest)")
        if abs(mean - 1.0) > 0.25:
            print("WARNING: |a| far from 1 g — orientation / scale check.", file=sys.stderr)
        return 0
    finally:
        mpu.close()


def cmd_live() -> int:
    mpu = MPU6050()
    try:
        print(f"{'ax':>7} {'ay':>7} {'az':>7} {'|a|':>7}   (Ctrl+C to stop)")
        while True:
            try:
                s = mpu.read()
            except OSError as e:
                print(f"\nI2C glitch: {e} (retrying…)", flush=True)
                time.sleep(0.05)
                continue
            print(
                f"{s.ax:7.3f} {s.ay:7.3f} {s.az:7.3f} {s.mag:7.3f}",
                end="\r",
                flush=True,
            )
            time.sleep(0.05)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    finally:
        mpu.close()


def cmd_rate() -> int:
    mpu = MPU6050()
    errors = 0
    n = 0
    seconds = 2.0
    t0 = time.perf_counter()
    try:
        while time.perf_counter() - t0 < seconds:
            try:
                mpu.read()
                n += 1
            except OSError:
                errors += 1
        hz = n / max(time.perf_counter() - t0, 1e-6)
        print(f"rate = {hz:.1f} Hz over {seconds:.1f} s, i2c_errors = {errors}")
        if hz < 100:
            print("FAIL: expected ≥ 100 Hz", file=sys.stderr)
            return 1
        if errors:
            print("FAIL: I2C errors present", file=sys.stderr)
            return 1
        print("PASS")
        return 0
    finally:
        mpu.close()


def cmd_detect(log_path: str | None) -> int:
    mpu = MPU6050()
    det = FallImpactDetector()
    writer = None
    fh = None
    if log_path:
        path = Path(log_path)
        fh = path.open("w", newline="", encoding="utf-8")
        writer = csv.writer(fh)
        writer.writerow(["t", "ax", "ay", "az", "mag"])

    print("Detecting falls (wear-gated). Ctrl+C to stop.")
    period = 1.0 / cfg.IMU_SAMPLE_HZ
    next_t = time.perf_counter()
    try:
        while True:
            now = time.perf_counter()
            try:
                s = mpu.read(now)
            except OSError:
                next_t = time.perf_counter() + period
                time.sleep(period)
                continue
            if writer:
                writer.writerow([f"{s.t:.6f}", f"{s.ax:.4f}", f"{s.ay:.4f}", f"{s.az:.4f}", f"{s.mag:.4f}"])
            hit = det.push(s)
            if hit:
                emit(
                    build_event(
                        hit.type,
                        side=None,
                        peak_accel_g=round(hit.peak_accel_g, 3),
                        freefall_ms=round(hit.freefall_ms, 1),
                        imu_trace=hit.imu_trace,
                        confidence=hit.confidence,
                    )
                )
            next_t += period
            sleep = next_t - time.perf_counter()
            if sleep > 0:
                time.sleep(sleep)
            else:
                next_t = time.perf_counter()
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    finally:
        mpu.close()
        if fh:
            fh.close()


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="MPU-6050 tests")
    parser.add_argument("mode", choices=["check", "live", "rate", "detect"])
    parser.add_argument("--log", default=None, help="CSV path for detect mode")
    args = parser.parse_args(argv)

    if args.mode == "check":
        return cmd_check()
    if args.mode == "live":
        return cmd_live()
    if args.mode == "rate":
        return cmd_rate()
    return cmd_detect(args.log)


if __name__ == "__main__":
    raise SystemExit(main())
