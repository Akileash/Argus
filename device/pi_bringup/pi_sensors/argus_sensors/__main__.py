"""CLI: python -m argus_sensors [--sensors tof,scd41] [--tof-bus 3] [--scd-bus 1] ..."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .runner import run
from .scan import scan_bus
from .bus import I2CBus, SensorFault


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Argus Pi sensors — VL6180X wear + SCD41 air (NDJSON)"
    )
    ap.add_argument(
        "--sensors",
        default="tof,scd41",
        help="Comma-separated sensor names (default: tof,scd41). "
        "Also: mock_tof, mock_scd41",
    )
    ap.add_argument(
        "--bus",
        type=int,
        default=None,
        help="Override: put ALL sensors on this I2C bus (single-bus mode)",
    )
    ap.add_argument(
        "--tof-bus",
        type=int,
        default=3,
        help="I2C bus for VL6180X / mock_tof (default 3 on this hat)",
    )
    ap.add_argument(
        "--scd-bus",
        type=int,
        default=1,
        help="I2C bus for SCD41 / mock_scd41 (default 1 on this hat)",
    )
    ap.add_argument(
        "--mock",
        action="store_true",
        help="Run without hardware (scripted ToF + SCD41)",
    )
    ap.add_argument(
        "--log-dir",
        default=".",
        help="Directory for wear_events.jsonl / air_events.jsonl",
    )
    ap.add_argument(
        "--log-samples",
        action="store_true",
        help="Also append sample heartbeats to jsonl (noisy)",
    )
    ap.add_argument(
        "--quiet",
        action="store_true",
        help="Print raw NDJSON only (no HELMET ON / AIR prefixes)",
    )
    ap.add_argument(
        "--scan",
        action="store_true",
        help="Scan the I2C bus and exit (uses --bus, else --tof-bus)",
    )
    args = ap.parse_args(argv)

    if args.scan:
        if args.mock:
            print("Use without --mock to scan real hardware.", file=sys.stderr)
            return 2
        scan_n = args.bus if args.bus is not None else args.tof_bus
        try:
            bus = I2CBus(bus_num=scan_n)
        except SensorFault as exc:
            print(f"# {exc}", file=sys.stderr)
            return 1
        try:
            addrs, text = scan_bus(bus)
            print(text)
            return 0 if addrs else 1
        finally:
            bus.close()

    names = [n.strip() for n in args.sensors.split(",") if n.strip()]
    if not names:
        print("No sensors specified.", file=sys.stderr)
        return 2

    return run(
        sensor_names=names,
        bus_num=args.bus,
        tof_bus_num=args.tof_bus,
        scd_bus_num=args.scd_bus,
        mock=args.mock,
        log_dir=Path(args.log_dir),
        log_samples=args.log_samples,
        quiet=args.quiet,
    )


if __name__ == "__main__":
    raise SystemExit(main())
