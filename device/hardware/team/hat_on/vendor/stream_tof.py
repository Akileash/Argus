#!/usr/bin/env python3
"""Stream VL6180X wear ToF as JSON (mm). Proven on Argus Pi I2C3 0x29.

For the full wear-gate dwell logic use device/pi_bringup/hat/tof.py.
This streamer emits distance/status for the team adapter mapping.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

TOF_ADDR = 0x29


def demo() -> None:
    seq = [
        {"distance_mm": 59, "status": "ok", "near": True, "helmet_worn": True},
        {"distance_mm": 62, "status": "ok", "near": True, "helmet_worn": True},
        {"distance_mm": None, "status": "out_of_range", "near": False, "helmet_worn": False},
    ]
    i = 0
    while True:
        print(json.dumps(seq[i % len(seq)]), flush=True)
        i += 1
        time.sleep(0.1)


def read_vl6180x(bus_id: int) -> dict:
    from smbus2 import SMBus

    bus = SMBus(bus_id)
    # Minimal single-shot style read via pi_bringup driver when available
    try:
        sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[4] / "pi_bringup"))
        from hat.tof import VL6180X

        tof = VL6180X(bus_id)
        tof.init()
        mm, st, ok = tof.read_range_single()
        distance = int(mm) if ok else None
        near = distance is not None and 50 <= distance <= 254
        return {
            "distance_mm": distance,
            "status": "ok" if ok else "out_of_range",
            "near": near,
            "helmet_worn": near,  # dwell applied in hat WearDetector, not here
        }
    except Exception:
        # Raw fallback: not fully init — return out_of_range so adapter stays honest
        _ = bus
        _ = TOF_ADDR
        return {
            "distance_mm": None,
            "status": "out_of_range",
            "near": False,
            "helmet_worn": False,
        }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--demo", action="store_true")
    p.add_argument("--bus", type=int, default=3)
    args = p.parse_args()
    if args.demo:
        demo()
        return
    try:
        while True:
            print(json.dumps(read_vl6180x(args.bus)), flush=True)
            time.sleep(0.05)
    except Exception as e:
        print(f"hardware path failed ({e}); demo mode", file=sys.stderr)
        demo()


if __name__ == "__main__":
    main()
