"""Mock sensors for --mock (no hardware / no smbus2)."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from ..base import Sensor
from ..bus import I2CBus
from .. import events
from ..sinks import Sink
from .vl6180x import (
    MIN_MM,
    NEAR_MM,
    ON_DWELL_MS,
    OFF_DWELL_MS,
    HEARTBEAT_MS,
    SAMPLE_GAP_MS,
    WearDetector,
)


class MockTofSensor(Sensor):
    """Scripted ToF: far → near 6s → far 4s → repeat. Exercises ON/OFF."""

    name = "mock_tof"
    address = 0x29
    heartbeat_ms = HEARTBEAT_MS
    poll_gap_ms = float(SAMPLE_GAP_MS)
    fails_before_recover = 8
    recover_cooldown_ms = 2000

    def __init__(self, bus: I2CBus, sink: Sink) -> None:
        super().__init__(bus, sink)
        self.detector = WearDetector()
        self._t0 = time.monotonic()
        self._snap = self.detector.snapshot(self._t0)

    def init_sensor(self) -> bool:
        self._t0 = time.monotonic()
        return True

    def _scripted_mm(self, now: float) -> tuple:
        elapsed = (now - self._t0) % 11.0
        if 1.0 <= elapsed < 7.0:
            return 40, 0, False
        return None, 7, False

    def make_hello(self, now: float) -> Dict[str, Any]:
        s = self._snap
        return events.hello_tof(
            helmet_worn=s.helmet_worn,
            state=s.state,
            distance_mm=s.distance_mm,
            status=s.status,
            near=s.near,
            sensor_ok=self.sensor_ok,
            recoveries=self.recoveries,
            near_mm_min=MIN_MM,
            near_mm_max=NEAR_MM,
            on_dwell_ms=ON_DWELL_MS,
            off_dwell_ms=OFF_DWELL_MS,
            now=now,
        )

    def make_sample(self, now: float) -> Optional[Dict[str, Any]]:
        s = self.detector.snapshot(now)
        self._snap = s
        return events.sample_tof(
            helmet_worn=s.helmet_worn,
            state=s.state,
            distance_mm=s.distance_mm,
            status=s.status,
            near=s.near,
            sensor_ok=self.sensor_ok,
            recoveries=self.recoveries,
            on_in_ms=s.on_in_ms,
            off_in_ms=s.off_in_ms,
            now=now,
        )

    def poll(self, now: float) -> List[Dict[str, Any]]:
        mm, status, fault = self._scripted_mm(now)
        self.note_ok()
        snap = self.detector.update(mm=mm, status=status, fault=fault, now=now)
        self._snap = snap
        if snap.changed:
            return [
                events.wear_event(
                    helmet_worn=snap.helmet_worn,
                    state=snap.state,
                    distance_mm=snap.distance_mm,
                    status=snap.status,
                    near=snap.near,
                    sensor_ok=True,
                    recoveries=self.recoveries,
                    on_in_ms=snap.on_in_ms,
                    off_in_ms=snap.off_in_ms,
                    now=now,
                )
            ]
        return []


class MockScdSensor(Sensor):
    """Emits an air_sample about every 5 s with slowly rising CO2."""

    name = "mock_scd41"
    address = 0x62
    heartbeat_ms = 2000
    poll_gap_ms = 200.0
    fails_before_recover = 8
    recover_cooldown_ms = 5000

    def __init__(self, bus: I2CBus, sink: Sink) -> None:
        super().__init__(bus, sink)
        self.read_count = 0
        self._last_air = 0.0
        self.last_co2 = 400
        self.last_temp = 23.5
        self.last_rh = 40.0
        self.last_valid = False

    def init_sensor(self) -> bool:
        return True

    def make_hello(self, now: float) -> Dict[str, Any]:
        return events.hello_scd(
            co2_ppm=None,
            temp_c=None,
            rh_pct=None,
            sensor_ok=self.sensor_ok,
            reads=self.read_count,
            recoveries=self.recoveries,
            now=now,
        )

    def make_sample(self, now: float) -> Optional[Dict[str, Any]]:
        return events.sample_scd(
            co2_ppm=self.last_co2 if self.last_valid else None,
            temp_c=self.last_temp if self.last_valid else None,
            rh_pct=self.last_rh if self.last_valid else None,
            sensor_ok=self.sensor_ok,
            reads=self.read_count,
            recoveries=self.recoveries,
            now=now,
        )

    def poll(self, now: float) -> List[Dict[str, Any]]:
        self.note_ok()
        if now - self._last_air < 5.0:
            return []
        self._last_air = now
        self.read_count += 1
        self.last_co2 = 400 + (self.read_count * 7) % 200
        self.last_temp = 23.0 + (self.read_count % 5) * 0.1
        self.last_rh = 40.0 + (self.read_count % 3) * 0.5
        self.last_valid = True
        return [
            events.air_sample(
                co2_ppm=self.last_co2,
                temp_c=self.last_temp,
                rh_pct=self.last_rh,
                sensor_ok=True,
                reads=self.read_count,
                recoveries=self.recoveries,
                now=now,
            )
        ]
