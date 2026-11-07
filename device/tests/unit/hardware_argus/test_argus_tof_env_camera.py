from __future__ import annotations

import time

import pytest

from argus.contracts import PROTOCOL_FOR, ModuleContext, MonotonicClock
from hardware.argus import camera as cam_mod
from hardware.argus import env as env_mod
from hardware.argus import hat_on as tof_mod


def ctx(**opts):
    return ModuleContext(clock=MonotonicClock(), options=opts)


# ---------------------------------------------------------------- hat_on
class FakeTof:
    def __init__(self, values):
        self.values = list(values)
        self.started_with = None

    def start(self, budget):
        self.started_with = budget

    def range_mm(self):
        v = self.values.pop(0) if len(self.values) > 1 else self.values[0]
        if isinstance(v, Exception):
            raise v
        return v

    def stop(self):
        pass


def test_tof_status_mapping():
    dev = FakeTof([85, 8190, 8191, 1500, RuntimeError("timeout")])
    d = tof_mod.Vl53HatOn(ctx(), device=dev)
    assert isinstance(d, PROTOCOL_FOR["hat_on"])
    got = [d.poll_once() for _ in range(5)]
    assert [(s.status, s.range_mm) for s in got] == [
        ("ok", 85.0), ("out_of_range", None), ("out_of_range", None), ("out_of_range", None), ("error", None),
    ]
    assert d.health().error_count == 1


def test_tof_start_timing_budget_and_thread():
    dev = FakeTof([100])
    d = tof_mod.Vl53HatOn(ctx(timing_budget_us=50000, poll_interval_ms=5), device=dev)
    d.start()
    time.sleep(0.05)
    t0 = time.perf_counter()
    out = d.read()
    assert time.perf_counter() - t0 < 0.005
    assert dev.started_with == 50000 and out and out[0].status == "ok"
    assert d.health().ok
    d.stop()


def test_tof_init_failure_does_not_raise():
    class Bad(FakeTof):
        def start(self, b):
            raise OSError("no device")

    d = tof_mod.Vl53HatOn(ctx(), device=Bad([1]))
    d.start()
    assert not d.health().ok and d.health().error_count == 1 and d.read() == []


# ---------------------------------------------------------------- env
class FakeScd:
    def __init__(self):
        self.ready = False
        self.offset = "unset"

    def start(self, off):
        self.offset = off

    def data_ready(self):
        return self.ready

    def values(self):
        self.ready = False
        return 612.0, 24.5, 41.2

    def stop(self):
        pass


def test_env_poll_only_when_ready():
    dev = FakeScd()
    e = env_mod.Scd41Env(ctx(temperature_offset_c=4.0), device=dev)
    assert isinstance(e, PROTOCOL_FOR["env"])
    assert e.poll_once() is None
    dev.ready = True
    s = e.poll_once()
    assert (s.co2_ppm, s.temp_c, s.rh_pct) == (612.0, 24.5, 41.2)
    assert len(e.read()) == 1 and e.read() == []


def test_env_start_applies_offset_and_thread():
    dev = FakeScd()
    dev.ready = True
    e = env_mod.Scd41Env(ctx(temperature_offset_c=4.0, poll_interval_s=0.005), device=dev)
    e.start()
    time.sleep(0.05)
    assert dev.offset == 4.0
    assert e.read() and e.health().ok
    e.stop()


def test_hat_on_default_rate_is_10hz_even_when_the_read_blocks():
    class SlowTof:
        def start(self, budget):
            pass

        def range_mm(self):
            time.sleep(0.033)  # a continuous-mode read waits for the next measurement
            return 60

        def stop(self):
            pass

    t = tof_mod.Vl53HatOn(ctx(), device=SlowTof())
    assert t._period_s == pytest.approx(0.1)
    t.start()
    time.sleep(2.0)
    n = len(t.read())
    t.stop()
    assert 16 <= n <= 22, n  # ~10 Hz, bench expects 8-12


def test_camera_ring_sizing_default_ge_30s():
    c = cam_mod.PiCamera(ctx())
    assert c._ring_s >= 30
    assert isinstance(c, PROTOCOL_FOR["camera"])


def test_camera_ring_s_alias_and_no_timestamp_clock_option():
    assert cam_mod.PiCamera(ctx(ring_s=35))._ring_s == 35
    assert cam_mod.PiCamera(ctx(ring_seconds=33, ring_s=35))._ring_s == 33
    assert not hasattr(cam_mod, "boottime_offset_ns")


def test_create_factories_do_not_touch_hardware():
    import hardware.argus.camera
    import hardware.argus.env
    import hardware.argus.haptics
    import hardware.argus.hat_on
    import hardware.argus.imu
    import hardware.argus.range

    for name, mod in [("imu", hardware.argus.imu), ("range", hardware.argus.range), ("hat_on", hardware.argus.hat_on),
                      ("env", hardware.argus.env), ("haptics", hardware.argus.haptics), ("camera", hardware.argus.camera)]:
        obj = mod.create(ctx())
        assert isinstance(obj, PROTOCOL_FOR[name]), name
