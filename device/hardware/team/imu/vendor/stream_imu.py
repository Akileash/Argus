#!/usr/bin/env python3
"""Stream Pololu MinIMU-9 v3 samples as JSON lines (g, deg/s) on Argus Pi (I2C3).

L3GD20H gyro @0x6B (WHO_AM_I 0xD7) + LSM303D accel @0x1D (WHO_AM_I 0x49), values from
the ST datasheets. Same register setup as device/pi_bringup/hat/minimu9.py (accel +-16 g,
gyro +-500 dps, 400 Hz internal rate, ~50 Hz filters); this copy is standalone.
Replaces the MPU-6050 version (damaged 2026-11-08; archived driver in hat/archive/mpu6050.py).
Do not run while hat.main is running: both would open the same chips.
"""
from __future__ import annotations
import argparse, json, struct, sys, time
try:
    from smbus2 import SMBus
except ImportError:
    SMBus = None  # type: ignore

GYRO_ADDR = 0x6B
ACCEL_ADDR = 0x1D
BUS = 3
ACCEL_G_PER_LSB = 0.000732   # LSM303D +-16 g: 0.732 mg/LSB
GYRO_DPS_PER_LSB = 0.0175    # L3GD20H +-500 dps: 17.5 mdps/LSB
AUTO_INC = 0x80              # sub-address bit 7 on both chips
OUT_X_L = 0x28               # 6 bytes, little-endian


def init(bus: "SMBus") -> None:
    who_g = bus.read_byte_data(GYRO_ADDR, 0x0F)
    who_a = bus.read_byte_data(ACCEL_ADDR, 0x0F)
    if (who_g, who_a) != (0xD7, 0x49):
        raise SystemExit(f"unexpected WHO_AM_I gyro=0x{who_g:02X} (want D7) accel=0x{who_a:02X} (want 49)")
    for reg, val in ((0x21, 0x00), (0x22, 0x00), (0x23, 0x90), (0x24, 0x00), (0x20, 0xAF)):
        bus.write_byte_data(GYRO_ADDR, reg, val)   # BDU, +-500 dps, 400 Hz / 50 Hz, then power on
    for reg, val in ((0x1F, 0x00), (0x21, 0xE0), (0x22, 0x00), (0x23, 0x00), (0x26, 0x02), (0x20, 0x8F)):
        bus.write_byte_data(ACCEL_ADDR, reg, val)  # +-16 g, 50 Hz anti-alias, 400 Hz, mag off, BDU
    time.sleep(0.1)


def read_once(bus: "SMBus") -> dict:
    a = bus.read_i2c_block_data(ACCEL_ADDR, OUT_X_L | AUTO_INC, 6)
    g = bus.read_i2c_block_data(GYRO_ADDR, OUT_X_L | AUTO_INC, 6)
    ax, ay, az = struct.unpack("<hhh", bytes(a))
    gx, gy, gz = struct.unpack("<hhh", bytes(g))
    return {
        "ax": round(ax * ACCEL_G_PER_LSB, 4),
        "ay": round(ay * ACCEL_G_PER_LSB, 4),
        "az": round(az * ACCEL_G_PER_LSB, 4),
        "gx": round(gx * GYRO_DPS_PER_LSB, 3),
        "gy": round(gy * GYRO_DPS_PER_LSB, 3),
        "gz": round(gz * GYRO_DPS_PER_LSB, 3),
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
    init(bus)
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
