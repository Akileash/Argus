"""SCD41 CRC, conversion, and recovery tests."""

from __future__ import annotations

import time

from argus_sensors.bus import FakeBus, I2CBus
from argus_sensors.sensors.scd41 import (
    crc8,
    check_crc_word,
    temp_from_raw,
    rh_from_raw,
    SCD41Sensor,
    CMD_GET_DATA_READY,
    CMD_READ_MEASUREMENT,
    CMD_STOP_PERIODIC,
    CMD_START_PERIODIC,
)


class CollectSink:
    def __init__(self) -> None:
        self.events = []

    def emit(self, evt):
        self.events.append(evt)

    def close(self):
        pass


def test_crc8_known():
    assert crc8(bytes([0xBE, 0xEF])) == 0x92
    assert check_crc_word(0xBE, 0xEF, 0x92)


def test_temp_rh_conversion():
    assert abs(temp_from_raw(0) - (-45.0)) < 1e-6
    assert abs(rh_from_raw(0) - 0.0) < 1e-6
    assert abs(temp_from_raw(0xFFFF) - 130.0) < 0.01
    assert abs(rh_from_raw(0xFFFF) - 100.0) < 0.01


def _scd_handler_factory(state):
    def handler(op, payload, read_len):
        cmd = (payload[0] << 8) | payload[1] if len(payload) >= 2 else 0
        if op == "write":
            if cmd == CMD_STOP_PERIODIC:
                state["stopped"] = True
                state["started"] = False
            elif cmd == CMD_START_PERIODIC:
                state["started"] = True
                state["t0"] = time.monotonic()
            return []
        if op == "write_read":
            if cmd == CMD_GET_DATA_READY:
                ready = state.get("started") and (time.monotonic() - state.get("t0", 0) > 0.05)
                word = 0x0001 if ready else 0x0000
                msb, lsb = (word >> 8) & 0xFF, word & 0xFF
                return [msb, lsb, crc8(bytes([msb, lsb]))]
            if cmd == CMD_READ_MEASUREMENT:
                co2 = 400
                temp_raw = 0x6666
                rh_raw = 0x6666
                words = [co2, temp_raw, rh_raw]
                out = []
                for w in words:
                    msb, lsb = (w >> 8) & 0xFF, w & 0xFF
                    out.extend([msb, lsb, crc8(bytes([msb, lsb]))])
                return out
        raise OSError(f"unexpected {op} {payload}")

    return handler


def test_scd41_emits_air_sample():
    fake = FakeBus()
    state = {}
    fake.set_command_device(0x62, _scd_handler_factory(state))
    bus = I2CBus(backend=fake)
    sink = CollectSink()
    sensor = SCD41Sensor(bus, sink)
    import argus_sensors.sensors.scd41 as mod

    old = mod.STOP_WAIT_S
    mod.STOP_WAIT_S = 0.0
    try:
        sensor.start()
        assert any(e["type"] == "hello" for e in sink.events)
        sensor._last_poll_mono = 0.0
        time.sleep(0.06)
        sensor.tick(time.monotonic())
        types = [e["type"] for e in sink.events]
        assert "air_sample" in types
        air = next(e for e in sink.events if e["type"] == "air_sample")
        assert air["co2_ppm"] == 400
        assert air["reads"] == 1
    finally:
        mod.STOP_WAIT_S = old


def test_scd41_discards_co2_zero():
    fake = FakeBus()

    def handler(op, payload, read_len):
        cmd = (payload[0] << 8) | payload[1]
        if op == "write":
            return []
        if cmd == CMD_GET_DATA_READY:
            return [0x00, 0x01, crc8(bytes([0x00, 0x01]))]
        if cmd == CMD_READ_MEASUREMENT:
            out = []
            for w in (0, 0x6666, 0x6666):
                msb, lsb = (w >> 8) & 0xFF, w & 0xFF
                out.extend([msb, lsb, crc8(bytes([msb, lsb]))])
            return out
        raise OSError("bad")

    fake.set_command_device(0x62, handler)
    bus = I2CBus(backend=fake)
    sink = CollectSink()
    sensor = SCD41Sensor(bus, sink)
    sensor.sensor_ok = True
    sensor._started = True
    sensor._last_poll_mono = 0.0
    sensor.poll(time.monotonic())
    assert all(e.get("type") != "air_sample" for e in sink.events)
    assert sensor.read_count == 0
