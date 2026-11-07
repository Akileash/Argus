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


# ---------------------------------------------------------------- camera
class FakeBackend:
    def __init__(self, fps=30, offset_ns=0):
        self.cb = None
        self.dumped = []
        self.fps = fps

    def start(self, on_frame):
        self.cb = on_frame

    def dump(self, path):
        with open(path, "wb") as f:
            f.write(b"\x00" * 1000)
        self.dumped.append(path)

    def stop(self):
        pass

    def feed(self, t0_native_ns, n, kf_every=30):
        for i in range(n):
            self.cb(t0_native_ns + i * 1_000_000_000 // self.fps, i % kf_every == 0)


def fake_mux(calls):
    def mux(args):
        calls.append(args)
        with open(args[-1], "wb") as f:
            f.write(b"mp4data")

    return mux


def test_camera_ring_sizing_default_ge_30s():
    c = cam_mod.PiCamera(ctx())
    assert c._ring_frames >= 30 * 30
    assert isinstance(c, PROTOCOL_FOR["camera"])


def test_camera_timestamp_offset_mapping():
    be = FakeBackend()
    c = cam_mod.PiCamera(ctx(), backend=be, offset_fn=lambda: 5_000_000_000)
    c.start()
    be.feed(105_000_000_000, 3)  # boottime stamps; mono = boottime - 5 s
    assert [t for t, _ in c._idx][0] == 100_000_000_000
    c2 = cam_mod.PiCamera(ctx(timestamp_clock="monotonic"), backend=FakeBackend(), offset_fn=lambda: 5_000_000_000)
    c2.start()
    c2._backend.feed(105_000_000_000, 1)
    assert c2._idx[0][0] == 105_000_000_000


def test_camera_save_clip_ffmpeg_copy(tmp_path):
    be, calls = FakeBackend(), []
    c = cam_mod.PiCamera(ctx(), backend=be, muxer=fake_mux(calls), offset_fn=lambda: 0, sleep=lambda s: None)
    c.start()
    t0 = 1_000_000_000_000
    be.feed(t0, 300)  # 10 s at 30 fps, keyframe every second
    out = tmp_path / "sub" / "clip.mp4"
    info = c.save_clip(t0 + 2_000_000_000, t0 + 8_000_000_000, str(out))
    args = calls[0]
    assert args[0] == "ffmpeg" and "-c" in args and args[args.index("-c") + 1] == "copy"
    assert args[args.index("-f") + 1] == "h264" and args[-1] == str(out)
    assert float(args[args.index("-ss") + 1]) == pytest.approx(2.0)
    assert float(args[args.index("-t") + 1]) == pytest.approx(6.0)
    assert info.path == str(out) and info.bytes == len(b"mp4data")
    assert info.duration_s == pytest.approx(6.0) and info.camera == "front"
    assert not (tmp_path / "sub" / "clip.mp4.raw.h264").exists()


def test_camera_clip_clamped_to_ring():
    be, calls = FakeBackend(), []
    c = cam_mod.PiCamera(ctx(max_post_wait_s=0), backend=be, muxer=fake_mux(calls), offset_fn=lambda: 0, sleep=lambda s: None)
    c.start()
    t0 = 5_000_000_000
    be.feed(t0, 90)
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        info = c.save_clip(t0 - 10_000_000_000, t0 + 60_000_000_000, os.path.join(d, "c.mp4"))
    assert info.t_start_ns == t0
    assert info.duration_s == pytest.approx(89 / 30, abs=0.01)


def test_camera_save_clip_errors_when_empty_or_stopped(tmp_path):
    c = cam_mod.PiCamera(ctx(max_post_wait_s=0), backend=FakeBackend(), muxer=fake_mux([]), sleep=lambda s: None)
    with pytest.raises(RuntimeError):
        c.save_clip(0, 1, str(tmp_path / "x.mp4"))
    c.start()
    with pytest.raises(RuntimeError):
        c.save_clip(0, 1, str(tmp_path / "x.mp4"))
    assert c.health().error_count == 2


def test_camera_health_tracks_frames():
    be = FakeBackend()
    c = cam_mod.PiCamera(ctx(), backend=be)
    c.start()
    be.feed(0, 1)
    h = c.health()
    assert h.ok and h.last_sample_age_ms is not None and h.last_sample_age_ms < 100
    c.stop()
    assert not c.health().ok


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
