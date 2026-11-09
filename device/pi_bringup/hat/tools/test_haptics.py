"""
DRV2605L haptic tests (ERM disc motors).

  python3 -m hat.tools.test_haptics left
  python3 -m hat.tools.test_haptics right
  python3 -m hat.tools.test_haptics both
  python3 -m hat.tools.test_haptics danger
  python3 -m hat.tools.test_haptics check   # probe I2C buses only
"""

from __future__ import annotations

import argparse
import subprocess
import time

from hat import config as cfg
from hat.haptics import Haptics


def cmd_check() -> int:
    print("I2C probe (expect 5a on bus 1 LEFT; 5a + 1d + 6b (MinIMU-9) + 29 on bus 3 RIGHT):\n")
    for bus in (1, 3):
        print(f"--- i2cdetect -y {bus} ---")
        try:
            out = subprocess.check_output(
                ["i2cdetect", "-y", str(bus)],
                stderr=subprocess.STDOUT,
                text=True,
            )
            print(out)
        except FileNotFoundError:
            print("i2cdetect not installed (apt install i2c-tools)")
            return 1
        except subprocess.CalledProcessError as e:
            print(e.output or e)
            if bus == 3:
                print(
                    "Bus 3 missing → add to /boot/firmware/config.txt:\n"
                    "  dtoverlay=i2c3,pins_4_5\n"
                    "then: sudo reboot"
                )
    return 0


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="DRV2605L haptic tests")
    parser.add_argument(
        "mode",
        choices=["left", "right", "both", "danger", "check"],
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if args.mode == "check":
        return cmd_check()

    h = Haptics(dry_run=args.dry_run)
    try:
        available = set(h._devs.keys())
        print(f"Boards online: {sorted(available) or '(none)'}")

        if args.mode in ("left", "right", "both"):
            need = {"left", "right"} if args.mode == "both" else {args.mode}
            missing = need - available
            if missing and not args.dry_run:
                print(
                    f"FAIL: {sorted(missing)} DRV2605L not on I2C — "
                    f"nothing to buzz. Fix wiring first."
                )
                if "left" in missing:
                    print(
                        "LEFT should be on bus 1: "
                        "i2cdetect -y 1 must show 5a.\n"
                        "  red→3.3V  black→GND  blue→SDA (pin 3)  yellow→SCL (pin 5)"
                    )
                if "right" in missing:
                    print("RIGHT should be on bus 3: i2cdetect -y 3 must show 5a.")
                return 1
            print(f"Buzzing {args.mode} (effect {cfg.HAPTIC_EFFECT_ID})…")
            print(f">>> BUZZING   motor={args.mode}  NOW")
            h.warn(args.mode)
            time.sleep(1.2)
            print(">>> BUZZ OK")
        else:
            print("Danger pattern LEFT then RIGHT…")
            for side in ("left", "right"):
                if side not in available and not args.dry_run:
                    print(f"SKIP {side} — board offline")
                    continue
                print(f">>> BUZZING   motor={side}  NOW")
                h.warn(side)
                time.sleep(1.3)
            print(">>> BUZZ OK")
        return 0
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    finally:
        h.close()


if __name__ == "__main__":
    raise SystemExit(main())
