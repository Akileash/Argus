#!/usr/bin/env python3
"""DRV2605L haptics stdin protocol: H,<motor>,<pwm>  motor=top|back|left|right|both."""
from __future__ import annotations
import sys, time

def map_motor(name: str) -> str:
    n = name.strip().lower()
    if n in ("top", "left", "l"):
        return "left"
    if n in ("back", "right", "r"):
        return "right"
    if n == "both":
        return "both"
    return n

def buzz(side: str, intensity: float) -> None:
    try:
        sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[4] / "pi_bringup"))
        from hat.haptics import Haptics
        h = Haptics()
        if side == "both":
            h.warn("left"); h.warn("right")
        else:
            h.warn(side)
    except Exception as e:
        print(f"buzz demo only ({e}): side={side} intensity={intensity}", file=sys.stderr, flush=True)
        time.sleep(0.15)

def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # H,motor,pwm
        parts = [x.strip() for x in line.replace(" ", "").split(",")]
        if len(parts) < 3 or parts[0].upper() != "H":
            print(f"skip: {line}", file=sys.stderr, flush=True)
            continue
        motor = map_motor(parts[1])
        try:
            pwm = float(parts[2])
        except ValueError:
            pwm = 1.0
        buzz(motor, pwm)

if __name__ == "__main__":
    main()
