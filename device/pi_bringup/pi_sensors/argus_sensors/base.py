"""Sensor base class with shared fail-streak / soft-recover logic."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .bus import I2CBus, SensorFault
from . import events
from .sinks import Sink


class Sensor:
    """Subclass this to add a new sensor.

    Required:
      name: short id used by --sensors (e.g. "tof", "scd41")
      address: I2C 7-bit address
      init_sensor() -> bool
      poll(now) -> list of events to emit (may be empty)

    Optional overrides:
      recover() — default re-runs init_sensor with cooldown
      heartbeat_ms, poll_gap_ms, fails_before_recover, recover_cooldown_ms
    """

    name: str = "sensor"
    address: int = 0x00
    heartbeat_ms: int = 2000
    poll_gap_ms: float = 50.0
    fails_before_recover: int = 8
    recover_cooldown_ms: int = 2000

    def __init__(self, bus: I2CBus, sink: Sink) -> None:
        self.bus = bus
        self.sink = sink
        self.sensor_ok = False
        self.fail_streak = 0
        self.recoveries = 0
        self._last_recover_mono = 0.0
        self._last_heartbeat_mono = 0.0
        self._started = False

    def init_sensor(self) -> bool:
        raise NotImplementedError

    def poll(self, now: float) -> List[Dict[str, Any]]:
        """One iteration. Return events to emit (not yet stamped if you prefer)."""
        raise NotImplementedError

    def make_hello(self, now: float) -> Dict[str, Any]:
        raise NotImplementedError

    def make_sample(self, now: float) -> Optional[Dict[str, Any]]:
        return None

    def recover(self, now: float) -> Optional[Dict[str, Any]]:
        if (now - self._last_recover_mono) * 1000.0 < self.recover_cooldown_ms:
            return None
        self._last_recover_mono = now
        self.recoveries += 1
        ok = False
        try:
            ok = bool(self.init_sensor())
        except SensorFault:
            ok = False
        self.sensor_ok = ok
        self.fail_streak = 0
        return events.sensor_recover(ok=ok, recoveries=self.recoveries, now=now)

    def note_ok(self) -> None:
        self.fail_streak = 0
        self.sensor_ok = True

    def note_fault(self, now: float) -> Optional[Dict[str, Any]]:
        self.fail_streak += 1
        self.sensor_ok = False
        if self.fail_streak >= self.fails_before_recover:
            return self.recover(now)
        return None

    def start(self) -> None:
        now = time.monotonic()
        try:
            self.sensor_ok = bool(self.init_sensor())
        except SensorFault:
            self.sensor_ok = False
        self.sink.emit(self.make_hello(now))
        self._last_heartbeat_mono = now
        self._started = True

    def tick(self, now: Optional[float] = None) -> None:
        if now is None:
            now = time.monotonic()
        if not self._started:
            self.start()
            return
        try:
            for evt in self.poll(now):
                if evt:
                    self.sink.emit(evt)
        except SensorFault:
            rec = self.note_fault(now)
            if rec:
                self.sink.emit(rec)
        if (now - self._last_heartbeat_mono) * 1000.0 >= self.heartbeat_ms:
            self._last_heartbeat_mono = now
            sample = self.make_sample(now)
            if sample:
                self.sink.emit(sample)

    def sleep_gap(self) -> None:
        time.sleep(self.poll_gap_ms / 1000.0)
