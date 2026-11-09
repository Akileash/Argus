#!/usr/bin/env python3
"""Stream SCD41 air as JSON. Proven via pi_sensors on I2C1 0x62."""
from __future__ import annotations

import argparse
import json
import sys
import time


def demo() -> None:
    while True:
        print(json.dumps({"co2_ppm": 555, "temp_c": 25.29, "rh_pct": 22.71}), flush=True)
        time.sleep(5.0)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--demo", action="store_true")
    p.add_argument("--bus", type=int, default=1)
    args = p.parse_args()
    if args.demo:
        demo()
        return
    try:
        root = __import__("pathlib").Path(__file__).resolve().parents[4] / "pi_bringup" / "pi_sensors"
        sys.path.insert(0, str(root))
        from argus_sensors.bus import I2CBus
        from argus_sensors.sensors.scd41 import SCD41

        bus = I2CBus(args.bus)
        scd = SCD41(bus)
        scd.stop_periodic_measurement()
        scd.start_periodic_measurement()
        while True:
            if not scd.get_data_ready():
                time.sleep(0.2)
                continue
            co2, temp_c, rh_pct = scd.read_measurement()
            print(
                json.dumps(
                    {
                        "co2_ppm": int(co2),
                        "temp_c": round(float(temp_c), 2),
                        "rh_pct": round(float(rh_pct), 2),
                    }
                ),
                flush=True,
            )
            time.sleep(1.0)
    except Exception as e:
        print(f"hardware path failed ({e}); demo mode", file=sys.stderr)
        demo()


if __name__ == "__main__":
    main()
