#!/usr/bin/env python3
"""
IMU-only fall → Pi Cam clip test.

Keeps a rolling ~20 s camera buffer. When the MPU-6050 reports a FALL,
saves last 20 s + next 10 s to ~/argus/clips/<event_id>.mp4

No ultrasonics / haptics.

Usage (from ~/argus):
  python3 -m hat.tools.test_imu_clip
  python3 -m hat.tools.test_imu_clip --simulate   # fake a fall after buffer fills
  python3 -m hat.tools.test_imu_clip --quick      # 5s pre / 3s post for a faster test
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
import uuid

from hat import config as cfg
from hat.camera import ClipRecorder
from hat.events import build_event, emit
from hat.imu import FallImpactDetector, MPU6050


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="IMU fall → camera clip")
    p.add_argument(
        "--simulate",
        action="store_true",
        help="After the buffer fills, fake a fall (no need to drop the hat)",
    )
    p.add_argument(
        "--quick",
        action="store_true",
        help="Use 5s pre / 3s post for a faster bring-up test",
    )
    args = p.parse_args(argv)

    if args.quick:
        cfg.CAM_PRE_S = 5.0
        cfg.CAM_POST_S = 3.0

    print(
        f"Clip window: {cfg.CAM_PRE_S:.0f}s before + {cfg.CAM_POST_S:.0f}s after a FALL"
    )
    print(f"Clips folder: {cfg.CLIPS_DIR}")

    cam = ClipRecorder()
    cam.start()
    if not cam.enabled or not cam._started:
        print(
            "ERROR: camera did not start. Reseat the ribbon, then:\n"
            "  rpicam-vid -t 2000 -n -o /tmp/t.h264 && ls -la /tmp/t.h264",
            file=sys.stderr,
        )
        return 1

    mpu = MPU6050()
    who = mpu.who_am_i()
    print(f"MPU WHO_AM_I=0x{who:02X}")
    det = FallImpactDetector()

    stop = False

    def _sig(*_a):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    fill_s = float(cfg.CAM_PRE_S) + 1.0
    print(f"Filling camera buffer (~{fill_s:.0f}s)… keep the board still, then drop/fall.")
    t0 = time.perf_counter()
    period = 1.0 / cfg.IMU_SAMPLE_HZ
    next_t = time.perf_counter()
    simulated = False
    out_path = None

    try:
        while not stop:
            now = time.perf_counter()

            # After buffer is full, optional fake fall
            if args.simulate and not simulated and (now - t0) >= fill_s:
                eid = str(uuid.uuid4())
                print(f"\n*** SIMULATED FALL  event_id={eid}")
                event = build_event("fall", peak_accel_g=6.0, freefall_ms=200.0, confidence="high")
                event["event_id"] = eid
                event["clip_pending"] = True
                emit(event)
                out_path = cam.capture(eid)  # blocks: dump + remux → .mp4
                simulated = True
                break

            mag = 0.0
            try:
                sample = mpu.read(now)
                mag = sample.mag
                hit = det.push(sample)
                if hit is not None and hit.type == "fall":
                    event = build_event(
                        "fall",
                        peak_accel_g=round(hit.peak_accel_g, 3),
                        freefall_ms=round(hit.freefall_ms, 1),
                        imu_trace=hit.imu_trace,
                        confidence=hit.confidence,
                    )
                    event["clip_pending"] = True
                    print(
                        f"\n*** FALL detected peak={hit.peak_accel_g:.1f}g "
                        f"ff={hit.freefall_ms:.0f}ms  → saving MP4…"
                    )
                    emit(event)
                    out_path = cam.capture(event["event_id"])
                    break
            except OSError as e:
                print(f"\nI2C error: {e}", file=sys.stderr)

            # status once a second
            if int(now) != int(now - period):
                age = now - t0
                print(
                    f"\r  t={age:5.1f}s  |a|={mag:.2f}  "
                    f"buffer={'ready' if age >= fill_s else 'filling'}   ",
                    end="",
                    flush=True,
                )

            next_t += period
            sleep = next_t - time.perf_counter()
            if sleep > 0:
                time.sleep(sleep)
            else:
                next_t = time.perf_counter()
    finally:
        print("\nStopping…")
        cam.stop()
        mpu.close()

    if out_path is not None and out_path.exists() and out_path.stat().st_size > 1000:
        print(f"\nDONE  {out_path}")
        print(f"size  {out_path.stat().st_size} bytes")
        return 0

    print(
        "No usable MP4. If the camera timed out, reseat the ribbon and retry.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
