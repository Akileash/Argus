"""Multi-sensor runner: one thread per sensor; dual-bus support for this hat."""

from __future__ import annotations

import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Type

from .base import Sensor
from .bus import FakeBus, I2CBus
from .sinks import FanoutSink, JsonlSink, Sink, StdoutSink
from .sensors import available_sensors

_LOG_MAP = {
    "tof": ("wear_events.jsonl", {"hello", "wear_event", "sensor_recover"}),
    "mock_tof": ("wear_events.jsonl", {"hello", "wear_event", "sensor_recover"}),
    "scd41": ("air_events.jsonl", {"hello", "air_sample", "sensor_recover"}),
    "mock_scd41": ("air_events.jsonl", {"hello", "air_sample", "sensor_recover"}),
}

# Which logical sensors ride which bus-role key
_TOF_NAMES = {"tof", "mock_tof"}
_SCD_NAMES = {"scd41", "mock_scd41"}


class SensorWorker(threading.Thread):
    def __init__(self, sensor: Sensor, stop_event: threading.Event) -> None:
        super().__init__(name=f"sensor-{sensor.name}", daemon=True)
        self.sensor = sensor
        self.stop_event = stop_event
        self.error: Optional[BaseException] = None

    def run(self) -> None:
        try:
            self.sensor.start()
            while not self.stop_event.is_set():
                self.sensor.tick()
                self.sensor.sleep_gap()
        except Exception as exc:  # noqa: BLE001 — isolate one sensor crash
            self.error = exc
            print(f"# sensor {self.sensor.name} crashed: {exc}", file=sys.stderr)
            traceback.print_exc()


def sensor_sink(
    name: str,
    stdout: StdoutSink,
    log_dir: Path,
    log_samples: bool,
    open_files: Dict[str, JsonlSink],
) -> FanoutSink:
    sinks: List[Sink] = [stdout]
    if name in _LOG_MAP:
        filename, types = _LOG_MAP[name]
        path = str(Path(log_dir) / filename)
        if path not in open_files:
            open_files[path] = JsonlSink(Path(path), types=types, log_samples=log_samples)
        sinks.append(open_files[path])
    return FanoutSink(sinks)


def _bus_for_name(
    key: str,
    buses: Dict[str, I2CBus],
    tof_bus: I2CBus,
    scd_bus: I2CBus,
    default_bus: I2CBus,
) -> I2CBus:
    if key in _TOF_NAMES:
        return tof_bus
    if key in _SCD_NAMES:
        return scd_bus
    return default_bus


def make_sensors(
    names: Sequence[str],
    tof_bus: I2CBus,
    scd_bus: I2CBus,
    default_bus: I2CBus,
    stdout: StdoutSink,
    log_dir: Path,
    log_samples: bool,
    open_files: Dict[str, JsonlSink],
) -> List[Sensor]:
    registry = available_sensors()
    sensors: List[Sensor] = []
    buses: Dict[str, I2CBus] = {}
    for name in names:
        key = name.strip().lower()
        if key not in registry:
            raise SystemExit(
                f"Unknown sensor '{name}'. Available: {', '.join(sorted(registry))}"
            )
        cls: Type[Sensor] = registry[key]
        bus = _bus_for_name(key, buses, tof_bus, scd_bus, default_bus)
        sink = sensor_sink(key, stdout, log_dir, log_samples, open_files)
        sensors.append(cls(bus, sink))
    return sensors


def run(
    sensor_names: Sequence[str],
    bus_num: Optional[int] = None,
    tof_bus_num: int = 3,
    scd_bus_num: int = 1,
    mock: bool = False,
    log_dir: Path = Path("."),
    log_samples: bool = False,
    quiet: bool = False,
) -> int:
    # --bus overrides both roles when set (single-bus bring-up / scan sibling)
    if bus_num is not None:
        tof_bus_num = bus_num
        scd_bus_num = bus_num

    if mock:
        mapped = []
        for n in sensor_names:
            if n == "tof":
                mapped.append("mock_tof")
            elif n == "scd41":
                mapped.append("mock_scd41")
            else:
                mapped.append(n)
        sensor_names = mapped
        fake = FakeBus()
        tof_bus = I2CBus(bus_num=tof_bus_num, backend=fake)
        scd_bus = tof_bus
        default_bus = tof_bus
        opened: List[I2CBus] = [tof_bus]
    else:
        # Open distinct buses only as needed; share object if same number
        cache: Dict[int, I2CBus] = {}

        def get_bus(n: int) -> I2CBus:
            if n not in cache:
                cache[n] = I2CBus(bus_num=n)
            return cache[n]

        tof_bus = get_bus(tof_bus_num)
        scd_bus = get_bus(scd_bus_num)
        default_bus = get_bus(tof_bus_num)
        opened = list(cache.values())

    Path(log_dir).mkdir(parents=True, exist_ok=True)
    stdout = StdoutSink(quiet=quiet)
    open_files: Dict[str, JsonlSink] = {}
    try:
        sensors = make_sensors(
            sensor_names,
            tof_bus,
            scd_bus,
            default_bus,
            stdout,
            Path(log_dir),
            log_samples,
            open_files,
        )
        stop = threading.Event()
        workers = [SensorWorker(s, stop) for s in sensors]
        bus_desc = (
            f"tof=/dev/i2c-{tof_bus_num} scd=/dev/i2c-{scd_bus_num}"
            if tof_bus_num != scd_bus_num
            else f"bus=/dev/i2c-{tof_bus_num}"
        )
        print(
            f"# starting sensors: {', '.join(s.name for s in sensors)}  "
            f"{bus_desc}  mock={mock}  log_dir={log_dir}",
            file=sys.stderr,
        )
        for w in workers:
            w.start()
        try:
            while any(w.is_alive() for w in workers):
                time.sleep(0.2)
        except KeyboardInterrupt:
            print("\n# stopping...", file=sys.stderr)
        stop.set()
        for w in workers:
            w.join(timeout=2.0)
        return 0
    finally:
        for s in open_files.values():
            s.close()
        for b in opened:
            b.close()
