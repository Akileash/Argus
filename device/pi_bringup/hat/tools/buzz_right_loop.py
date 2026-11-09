#!/usr/bin/env python3
"""Buzz RIGHT DRV2605L every 2 seconds until Ctrl+C.

  python3 -m hat.tools.buzz_right_loop
"""

from __future__ import annotations

import signal
import sys
import time

from smbus2 import SMBus

ADDR = 0x5A
BUS_ID = 3
INTERVAL_S = 2.0
BUZZ_S = 0.6


def _sig(_s, _f) -> None:
    raise KeyboardInterrupt


signal.signal(signal.SIGTERM, _sig)


def open_bus() -> SMBus:
    bus = SMBus(BUS_ID)
    # Wake, ERM, library 1
    bus.write_byte_data(ADDR, 0x01, 0x00)
    fb = bus.read_byte_data(ADDR, 0x1A)
    bus.write_byte_data(ADDR, 0x1A, fb & 0x7F)
    bus.write_byte_data(ADDR, 0x03, 1)
    c3 = bus.read_byte_data(ADDR, 0x1D)
    bus.write_byte_data(ADDR, 0x1D, c3 | 0x20)  # ERM open loop
    return bus


def buzz_rtp(bus: SMBus, seconds: float) -> None:
    """Continuous RTP buzz — more reliable feel than short ROM clicks."""
    bus.write_byte_data(ADDR, 0x01, 0x05)  # RTP mode
    bus.write_byte_data(ADDR, 0x02, 0x7F)  # full amplitude
    time.sleep(seconds)
    bus.write_byte_data(ADDR, 0x02, 0x00)
    bus.write_byte_data(ADDR, 0x01, 0x00)


def main() -> int:
    print(
        f"RIGHT DRV on i2c-{BUS_ID}: buzz {BUZZ_S}s every {INTERVAL_S}s. "
        "Ctrl+C to stop.",
        flush=True,
    )
    bus = None
    n = 0
    try:
        while True:
            n += 1
            try:
                if bus is None:
                    bus = open_bus()
                print(f">>> BUZZ right #{n}  NOW", flush=True)
                buzz_rtp(bus, BUZZ_S)
                print(f">>> quiet  ({INTERVAL_S}s)", flush=True)
            except OSError as e:
                print(f"[i2c error] {e} — reopening bus…", flush=True)
                try:
                    if bus is not None:
                        bus.close()
                except OSError:
                    pass
                bus = None
                time.sleep(0.3)
                continue
            time.sleep(max(0.0, INTERVAL_S - BUZZ_S))
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
        return 0
    finally:
        if bus is not None:
            try:
                bus.write_byte_data(ADDR, 0x02, 0x00)
                bus.write_byte_data(ADDR, 0x01, 0x40)  # standby
            except OSError:
                pass
            bus.close()


if __name__ == "__main__":
    raise SystemExit(main())
