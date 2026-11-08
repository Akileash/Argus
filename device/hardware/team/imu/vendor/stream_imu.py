#!/usr/bin/env python3
"""Stream MPU-6050 samples as JSON lines (g, deg/s). Proven on Argus Pi (I2C3)."""
from __future__ import annotations
import argparse, json, struct, sys, time
try:
    from smbus2 import SMBus
except ImportError:
    SMBus = None  # type: ignore

MPU_ADDR = 0x68
BUS = 3
ACCEL_LSB_PER_G = 2048.0
GYRO_LSB_PER_DPS = 32.8

def read_once(bus: "SMBus") -> dict:
    raw = bus.read_i2c_block_data(MPU_ADDR, 0x3B, 14)
    ax, ay, az = struct.unpack(">hhh", bytes(raw[0:6]))
    gx, gy, gz = struct.unpack(">hhh", bytes(raw[8:14]))
    return {
        "ax": round(ax / ACCEL_LSB_PER_G, 4),
        "ay": round(ay / ACCEL_LSB_PER_G, 4),
        "az": round(az / ACCEL_LSB_PER_G, 4),
        "gx": round(gx / GYRO_LSB_PER_DPS, 3),
        "gy": round(gy / GYRO_LSB_PER_DPS, 3),
        "gz": round(gz / GYRO_LSB_PER_DPS, 3),
    }

def demo_loop(hz: float) -> None:
    # Still-ish sample for offline team check
    dt = 1.0 / hz
    while True:
        print(json.dumps({"ax": 0.02, "ay": -0.01, "az": 0.99, "gx": 0.1, "gy": -0.2, "gz": 0.05}), flush=True)
        time.sleep(dt)

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--bus", type=int, default=BUS)
    p.add_argument("--hz", type=float, default=200.0)
    p.add_argument("--demo", action="store_true", help="print synthetic samples (no hardware)")
    args = p.parse_args()
    if args.demo or SMBus is None:
        demo_loop(args.hz)
        return
    bus = SMBus(args.bus)
    bus.write_byte_data(MPU_ADDR, 0x6B, 0x00)  # wake
    dt = 1.0 / args.hz
    while True:
        try:
            print(json.dumps(read_once(bus)), flush=True)
        except OSError as e:
            print(f"I2C error: {e}", file=sys.stderr, flush=True)
            time.sleep(0.05)
            continue
        time.sleep(dt)

if __name__ == "__main__":
    main()
