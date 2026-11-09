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
from hat.imu import FallImpactDetector
from hat.minimu9 import ImuIdentityError, MiniIMU9


def cmd_check() -> int:
    """Bring-up check for the MinIMU-9 (stop the bridge / hat service first).

    Only normal init writes happen (config registers); the rest is reads.
    Prints both WHO_AM_I values, resting |a| and gyro, and the measured rate.
    """
    try:
        imu = MiniIMU9()
    except ImuIdentityError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"FAIL: I2C error opening IMU: {e}\n  i2cdetect -y {cfg.I2C_BUS} must show "
              f"{cfg.IMU_GYRO_ADDR:02x} and {cfg.IMU_ACCEL_ADDR:02x}", file=sys.stderr)
        return 1
    rc = 0
    try:
        print(f"WHO_AM_I: {imu.who_am_i()}")
        print(f"config read-back: {'OK' if imu.check_config() else 'LOST (re-initialised; see above)'}")
        time.sleep(0.1)
        # Poll flat out for long enough to finish the gyro zero-rate calibration
        # (needs a still window of GYRO_CAL_WINDOW_S: hold the hat still).
        samples = []
        errors = 0
        t0 = time.perf_counter()
        secs = cfg.GYRO_CAL_WINDOW_S + 1.5
        while time.perf_counter() - t0 < secs:
            try:
                samples.append(imu.read())
            except OSError:
                errors += 1
        hz = len(samples) / max(time.perf_counter() - t0, 1e-6)
        n = len(samples)
        if n == 0:
            print("FAIL: no samples read", file=sys.stderr)
            return 1
        mean = sum(s.mag for s in samples) / n
        print(f"|a| mean over {n} samples = {mean:.3f} g  (expect ~1.00 at rest, within ~0.06)")
        print(f"axes at rest: a = ({sum(s.ax for s in samples)/n:+.3f}, {sum(s.ay for s in samples)/n:+.3f}, "
              f"{sum(s.az for s in samples)/n:+.3f}) g")
        if imu.gyro_bias is None:
            print("gyro: NOT calibrated (no still window): hold the hat still and re-run", file=sys.stderr)
            rc = 1
        else:
            b = imu.gyro_bias
            print(f"gyro zero-rate (measured bias) = ({b[0]:+.2f}, {b[1]:+.2f}, {b[2]:+.2f}) dps "
                  f"(datasheet level up to +-25 dps)")
            post = [s for s in samples if s.gx is not None]
            if post:
                m = [sum(getattr(s, k) for s in post) / len(post) for k in ("gx", "gy", "gz")]
                print(f"gyro after bias = ({m[0]:+.2f}, {m[1]:+.2f}, {m[2]:+.2f}) dps  (expect ~0)")
        print(f"poll rate flat-out = {hz:.0f} Hz, i2c_errors = {imu.i2c_errors} (read errors {errors})")
        if abs(mean - 1.0) > 0.25:
            print("FAIL: |a| far from 1 g: scale / range check.", file=sys.stderr)
            rc = 1
        if hz < cfg.IMU_SAMPLE_HZ:
            print(f"FAIL: poll rate below IMU_SAMPLE_HZ={cfg.IMU_SAMPLE_HZ:.0f}", file=sys.stderr)
            rc = 1
        if errors or imu.i2c_errors:
            print("WARNING: I2C errors present", file=sys.stderr)
        print("PASS" if rc == 0 else "FAIL")
        return rc
    finally:
        imu.close()


def cmd_live() -> int:
    imu = MiniIMU9()
    try:
        print(f"{'ax':>7} {'ay':>7} {'az':>7} {'|a|':>7}   (Ctrl+C to stop)")
        while True:
            try:
                s = imu.read()
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
        imu.close()


def cmd_rate() -> int:
    imu = MiniIMU9()
    errors = 0
    n = 0
    seconds = 2.0
    t0 = time.perf_counter()
    try:
        while time.perf_counter() - t0 < seconds:
            try:
                imu.read()
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
        imu.close()


def cmd_detect(log_path: str | None) -> int:
    imu = MiniIMU9()
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
                s = imu.read(now)
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
        imu.close()
        if fh:
            fh.close()


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="MinIMU-9 (L3GD20H + LSM303D) tests")
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
