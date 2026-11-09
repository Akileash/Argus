"""Soft-recover fail-streak / cooldown tests."""

from __future__ import annotations

import time

from argus_sensors.base import Sensor
from argus_sensors.bus import FakeBus, I2CBus, SensorFault
from argus_sensors import events


class CollectSink:
    def __init__(self) -> None:
        self.events = []

    def emit(self, evt):
        self.events.append(evt)

    def close(self):
        pass


class FlakySensor(Sensor):
    name = "flaky"
    address = 0x10
    heartbeat_ms = 10_000
    poll_gap_ms = 1.0
    fails_before_recover = 8
    recover_cooldown_ms = 100

    def __init__(self, bus, sink):
        super().__init__(bus, sink)
        self.inits = 0
        self.fail_polls = True

    def init_sensor(self) -> bool:
        self.inits += 1
        return True

    def make_hello(self, now):
        return events.stamp({"type": "hello", "device": "flaky"}, now)

    def make_sample(self, now):
        return None

    def poll(self, now):
        if self.fail_polls:
            raise SensorFault("boom")
        return []


def test_recover_after_8_failures():
    bus = I2CBus(backend=FakeBus())
    sink = CollectSink()
    s = FlakySensor(bus, sink)
    s.start()
    assert s.inits == 1
    t0 = time.monotonic()
    for i in range(8):
        s.tick(t0 + i * 0.01)
    recovers = [e for e in sink.events if e["type"] == "sensor_recover"]
    assert len(recovers) == 1
    assert recovers[0]["ok"] is True
    assert recovers[0]["recoveries"] == 1
    assert s.inits == 2


def test_recover_cooldown():
    bus = I2CBus(backend=FakeBus())
    sink = CollectSink()
    s = FlakySensor(bus, sink)
    s.recover_cooldown_ms = 1000
    s.start()
    t0 = time.monotonic()
    for i in range(8):
        s.tick(t0 + i * 0.001)
    assert s.recoveries == 1
    for i in range(8):
        s.tick(t0 + 0.01 + i * 0.001)
    assert s.recoveries == 1
    for i in range(8):
        s.tick(t0 + 1.1 + i * 0.001)
    assert s.recoveries == 2
