#!/usr/bin/env python3
"""Stream dual HC-SR04 as JSON (cm). Left=back_left/R2, Right=back_right/R3."""
from __future__ import annotations
import argparse, json, sys, time

def demo() -> None:
    while True:
        print(json.dumps({"sensor": "back_left", "dist_cm": 45.2}), flush=True)
        print(json.dumps({"sensor": "back_right", "dist_cm": 51.0}), flush=True)
        time.sleep(0.08)

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--demo", action="store_true")
    args = p.parse_args()
    if args.demo:
        demo()
        return
    try:
        from gpiozero import Device
        from gpiozero.pins.lgpio import LGPIOFactory
        Device.pin_factory = LGPIOFactory()
        from gpiozero import DigitalInputDevice, DigitalOutputDevice
    except Exception as e:
        print(f"gpio unavailable ({e}); use --demo", file=sys.stderr)
        demo()
        return

    # Pins match pi_bringup/hat/config.py
    sensors = [
        ("back_left", 23, 24),
        ("back_right", 20, 21),
    ]
    outs = []
    for name, trig, echo in sensors:
        outs.append((name, DigitalOutputDevice(trig, initial_value=False), DigitalInputDevice(echo, pull_up=False)))

    def dist_cm(trig, echo) -> float:
        trig.off(); time.sleep(2e-6); trig.on(); time.sleep(1e-5); trig.off()
        t0 = time.perf_counter()
        while echo.value == 0:
            if time.perf_counter() - t0 > 0.024:
                return 0.0
        rise = time.perf_counter()
        while echo.value == 1:
            if time.perf_counter() - rise > 0.024:
                return 0.0
        fall = time.perf_counter()
        d = (fall - rise) * 343.0 / 2.0 * 100.0
        if d < 5.0 or d > 300.0:
            return 0.0
        return round(d, 1)

    try:
        while True:
            for name, trig, echo in outs:
                print(json.dumps({"sensor": name, "dist_cm": dist_cm(trig, echo)}), flush=True)
            time.sleep(0.04)
    finally:
        for _, trig, echo in outs:
            trig.close(); echo.close()

if __name__ == "__main__":
    main()
