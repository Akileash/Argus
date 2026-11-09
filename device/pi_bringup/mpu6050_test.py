#!/usr/bin/env python3
"""
Quick MPU-6050 sanity check (I2C 0x68 on bus 1).

Wiring (shared with DRV2605L later):
  VCC → 3.3V  pin 1
  GND → GND   pin 6
  SDA → GPIO2 pin 3
  SCL → GPIO3 pin 5

Usage:
  cd ~/argus && source .venv/bin/activate
  python mpu6050_test.py
"""

from __future__ import annotations

import math
import struct
import sys
import time

from smbus2 import SMBus

I2C_BUS = 1
ADDR = 0x68
PWR_MGMT_1 = 0x6B
WHO_AM_I = 0x75
ACCEL_CONFIG = 0x1C
GYRO_CONFIG = 0x1B
ACCEL_XOUT_H = 0x3B
ACCEL_LSB_PER_G = 16384.0   # +/-2 g for a readable desk test
GYRO_LSB_PER_DPS = 131.0     # +/-250 dps


def main() -> int:
    bus = SMBus(I2C_BUS)
    try:
        who = bus.read_byte_data(ADDR, WHO_AM_I)
        print(f"WHO_AM_I = 0x{who:02X} (expect 0x68 or 0x70)")
        if who not in (0x68, 0x70):
            print("Unexpected device id — check wiring / address.", file=sys.stderr)
            return 1

        bus.write_byte_data(ADDR, PWR_MGMT_1, 0x00)  # wake
        time.sleep(0.1)
        bus.write_byte_data(ADDR, ACCEL_CONFIG, 0x00)  # +/-2 g
        bus.write_byte_data(ADDR, GYRO_CONFIG, 0x00)   # +/-250 dps

        print("Reading. Tilt / shake the sensor. Ctrl+C to stop.\n")
        print(f"{'ax':>7} {'ay':>7} {'az':>7} {'|a|':>7}  {'gx':>8} {'gy':>8} {'gz':>8}")
        while True:
            raw = bus.read_i2c_block_data(ADDR, ACCEL_XOUT_H, 14)
            ax, ay, az, _t, gx, gy, gz = struct.unpack(">hhhhhhh", bytes(raw))
            ax /= ACCEL_LSB_PER_G
            ay /= ACCEL_LSB_PER_G
            az /= ACCEL_LSB_PER_G
            gx /= GYRO_LSB_PER_DPS
            gy /= GYRO_LSB_PER_DPS
            gz /= GYRO_LSB_PER_DPS
            mag = math.sqrt(ax * ax + ay * ay + az * az)
            print(
                f"{ax:7.3f} {ay:7.3f} {az:7.3f} {mag:7.3f}  "
                f"{gx:8.1f} {gy:8.1f} {gz:8.1f}",
                end="\r",
                flush=True,
            )
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    except OSError as e:
        print(f"I2C error: {e}", file=sys.stderr)
        print("Is the MPU powered and on SDA/SCL (pins 3/5)?", file=sys.stderr)
        return 1
    finally:
        bus.close()


if __name__ == "__main__":
    raise SystemExit(main())
