#!/usr/bin/env python3
"""Smoke-test the Pi Cam rolling buffer + fall clip dump.

Usage (from ~/argus):
  python3 -m hat.tools.test_camera          # fill buffer 22s, simulate fall, save clip
  python3 -m hat.tools.test_camera --quick  # ~5s fill (shorter buffer for bring-up)
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid

from hat import config as cfg
from hat.camera import ClipRecorder


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Test Argus Pi Cam fall clip")
    p.add_argument(
        "--quick",
        action="store_true",
        help="Use a short 5s pre-roll for a faster test",
    )
    args = p.parse_args(argv)

    if args.quick:
        cfg.CAM_PRE_S = 5.0
        cfg.CAM_POST_S = 3.0

    rec = ClipRecorder()
    rec.start()
    if not rec.enabled or not rec._started:
        print("Camera did not start — check ribbon cable / rpicam-hello --list-cameras")
        return 1

    fill = float(cfg.CAM_PRE_S) + 2.0
    print(f"Filling rolling buffer for {fill:.0f}s (wave the camera around)…")
    time.sleep(fill)

    eid = str(uuid.uuid4())
    print(f"Simulating FALL event_id={eid}")
    rec.capture_async(eid)

    wait = float(cfg.CAM_POST_S) + 5.0
    print(f"Waiting {wait:.0f}s for post-roll + remux…")
    time.sleep(wait)
    rec.stop()

    mp4 = cfg.CLIPS_DIR / f"{eid}.mp4"
    h264 = cfg.CLIPS_DIR / f"{eid}.h264"
    for path in (mp4, h264):
        if path.exists() and path.stat().st_size > 1000:
            print(f"OK → {path} ({path.stat().st_size} bytes)")
            return 0
    print(
        "FAIL: no usable clip written.\n"
        "If you saw 'Camera frontend has timed out', reseat the Pi Cam\n"
        "ribbon (contacts toward HDMI side on Pi 4, blue tab facing\n"
        "ethernet on many boards) and retry: rpicam-hello --list-cameras",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
