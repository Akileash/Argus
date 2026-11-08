#!/usr/bin/env python3
"""First thing to run on the Pi after copying this package.

Checks I2C buses for this hat: VL6180X on bus 3, SCD41 on bus 1.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from argus_sensors.bus import I2CBus, SensorFault
from argus_sensors.scan import scan_bus, label
from argus_sensors.sensors.vl6180x import (
    VL6180X,
    IDENTIFICATION__MODEL_ID,
    MODEL_ID,
    DEFAULT_ADDR as TOF_ADDR,
)
from argus_sensors.sensors.scd41 import SCD41, DEFAULT_ADDR as SCD_ADDR, STOP_WAIT_S


def check_tof(bus: I2CBus) -> bool:
    print(f"\n=== VL6180X (0x29) on /dev/i2c-{bus.bus_num} ===")
    if not bus.probe(TOF_ADDR):
        print("FAIL  not found on bus. Check VIN=3.3V, GND, SDA/SCL (I2C3 pins 7/29).")
        return False
    try:
        drv = VL6180X(bus, TOF_ADDR)
        mid = drv.read_reg(IDENTIFICATION__MODEL_ID)
        print(f"  model_id = 0x{mid:02X} (expect 0x{MODEL_ID:02X})")
        if mid != MODEL_ID:
            print("FAIL  wrong model id — is this a VL6180X?")
            return False
        drv.init()
        drv.configure_default()
        checks = drv.read_reg(0x02D)
        drv.write_reg(0x02D, checks & ~0x01)
        drv.write_reg(0x015, 0x07)
        ok_reads = 0
        for i in range(10):
            mm, status, timed_out = drv.read_range_single(timeout_ms=500)
            if timed_out:
                print(f"  read {i+1}: TIMEOUT")
            else:
                print(f"  read {i+1}: mm={mm} status={status}")
                ok_reads += 1
            time.sleep(0.05)
        if ok_reads == 0:
            print("FAIL  all reads timed out. Check 3.3V power / wiring.")
            return False
        print(f"PASS  {ok_reads}/10 reads completed (status 7 = no target is normal)")
        return True
    except SensorFault as exc:
        print(f"FAIL  {exc}")
        return False


def check_scd(bus: I2CBus) -> bool:
    print(f"\n=== SCD41 (0x62) on /dev/i2c-{bus.bus_num} ===")
    if not bus.probe(SCD_ADDR):
        print("FAIL  not found on bus. Power from 3.3V. SDA/SCL on I2C1 pins 3/5.")
        return False
    try:
        drv = SCD41(bus, SCD_ADDR)
        print(f"  stopping periodic (waits {STOP_WAIT_S}s)...")
        drv.stop_periodic_measurement()
        drv.start_periodic_measurement()
        print("  waiting for first sample (~5 s)...")
        deadline = time.monotonic() + 12.0
        while time.monotonic() < deadline:
            try:
                if drv.get_data_ready():
                    co2, temp, rh = drv.read_measurement()
                    if co2 == 0:
                        time.sleep(0.2)
                        continue
                    print(f"  co2={co2} ppm  temp={temp:.2f} C  rh={rh:.2f}%")
                    print("PASS  got a valid air sample")
                    return True
            except SensorFault as exc:
                print(f"  transient: {exc}")
            time.sleep(0.2)
        print("FAIL  no sample within 12 s. Sensor may need power-cycle.")
        return False
    except SensorFault as exc:
        print(f"FAIL  {exc}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Argus Pi hardware check (dual-bus hat)")
    ap.add_argument("--tof-bus", type=int, default=3, help="I2C bus for VL6180X (default 3)")
    ap.add_argument("--scd-bus", type=int, default=1, help="I2C bus for SCD41 (default 1)")
    args = ap.parse_args()

    buses: list[I2CBus] = []
    try:
        print(f"Opening /dev/i2c-{args.tof_bus} (ToF) and /dev/i2c-{args.scd_bus} (SCD41) ...")
        try:
            tof_bus = I2CBus(bus_num=args.tof_bus)
            buses.append(tof_bus)
        except SensorFault as exc:
            print(f"FAIL  ToF bus: {exc}")
            return 1
        try:
            if args.scd_bus == args.tof_bus:
                scd_bus = tof_bus
            else:
                scd_bus = I2CBus(bus_num=args.scd_bus)
                buses.append(scd_bus)
        except SensorFault as exc:
            print(f"FAIL  SCD bus: {exc}")
            return 1

        for bus in (tof_bus,) if args.scd_bus == args.tof_bus else (tof_bus, scd_bus):
            addrs, text = scan_bus(bus)
            print(f"\n--- scan /dev/i2c-{bus.bus_num} ---")
            print(text)
            for a in addrs:
                if a not in (TOF_ADDR, SCD_ADDR):
                    print(f"  note: also found 0x{a:02X} ({label(a)}) — sharing the bus is fine")

        tof_ok = check_tof(tof_bus)
        scd_ok = check_scd(scd_bus)
        print("\n=== SUMMARY ===")
        print(f"  VL6180X (bus {args.tof_bus}): {'PASS' if tof_ok else 'FAIL'}")
        print(f"  SCD41   (bus {args.scd_bus}): {'PASS' if scd_ok else 'FAIL'}")
        if tof_ok and scd_ok:
            print(
                "\nAll good. Run:  python -m argus_sensors --tof-bus "
                f"{args.tof_bus} --scd-bus {args.scd_bus} --log-dir ./logs"
            )
            return 0
        return 1
    finally:
        seen = set()
        for b in buses:
            if id(b) not in seen:
                seen.add(id(b))
                b.close()


if __name__ == "__main__":
    raise SystemExit(main())
