"""Hardware contract tests (spec §5.6 row 1), parametrized by --module / --impl.

Checks: units in range, stationary IMU |a| ~ 1 g, monotonic timestamps, rate within +-20%, read()
under 5 ms, honest health(), no exception escapes. Default is every module with the mock impl;
on the Pi run e.g. ``uv run pytest tests/contract --module imu --impl team`` with the hat still.
"""

from __future__ import annotations

import statistics
import time

import pytest

from argus.contracts import PROTOCOL_FOR, SENSOR_IDS, STALE_AFTER_MS

STREAMING = ("imu", "range", "hat_on", "env")
# (min Hz, max Hz or None) per spec §5.2/§4: imu >= 400 Hz (-20%), range 5 sensors x 6.7 Hz, tof 10, env 0.2
RATES = {"imu": (320.0, None), "range": (26.7, 40.0), "hat_on": (8.0, 12.0), "env": (0.16, 0.24)}
WINDOW_S = {"imu": 3.0, "range": 3.0, "hat_on": 3.0, "env": 30.0}


def _streaming(module: str) -> None:
    if module not in STREAMING:
        pytest.skip(f"{module} is not a streaming module")


def test_satisfies_protocol(module, make_dut):
    h = make_dut()
    assert isinstance(h.inst, PROTOCOL_FOR[module])


def test_units_in_range(module, dut):
    _streaming(module)
    samples = dut.collect(WINDOW_S[module])
    assert samples, "no samples received"
    for s in samples:
        if module == "imu":
            assert all(abs(v) <= 16.5 for v in (s.ax, s.ay, s.az)), f"accel outside +-16 g (units?): {s}"
            assert all(abs(v) <= 1000.5 for v in (s.gx, s.gy, s.gz)), f"gyro outside +-1000 deg/s: {s}"
        elif module == "range":
            assert s.sensor_id in SENSOR_IDS
            assert s.range_mm is None or 20 <= s.range_mm <= 4500, f"range must be mm in 20..4500 or None: {s}"
        elif module == "hat_on":
            assert s.status in ("ok", "out_of_range", "error")
            assert (s.range_mm is None) == (s.status != "ok") or s.status == "error", s
            assert s.range_mm is None or 0 < s.range_mm < 2000, s
        elif module == "env":
            assert 0 < s.co2_ppm < 40000 and -40 < s.temp_c < 85 and 0 <= s.rh_pct <= 100, s


def test_imu_stationary_is_one_g(module, dut):
    if module != "imu":
        pytest.skip("imu only")
    s = dut.collect(2.0)
    mags = [(x.ax**2 + x.ay**2 + x.az**2) ** 0.5 for x in s]
    assert 0.9 <= statistics.median(mags) <= 1.1, "hold the hat still; |a| must be ~1 g (accel in g, not m/s^2)"
    assert statistics.mean(abs(x.gx) + abs(x.gy) + abs(x.gz) for x in s) < 15, "gyro must be deg/s, ~0 at rest"


def test_timestamps_monotonic(module, dut):
    _streaming(module)
    samples = dut.collect(min(WINDOW_S[module], 6.0))
    ts = [s.t_ns for s in samples]
    assert ts == sorted(ts), "timestamps must be non-decreasing across reads (monotonic clock)"
    assert all(isinstance(t, int) for t in ts)
    if module != "range":  # range sensors interleave but each stream is still ordered
        assert len(set(ts)) == len(ts), "duplicate timestamps"


def test_rate_within_spec(module, dut):
    _streaming(module)
    window = WINDOW_S[module]
    n = len(dut.collect(window))
    lo, hi = RATES[module]
    rate = n / window
    assert rate >= lo, f"{rate:.2f} Hz < {lo} Hz"
    if hi is not None:
        assert rate <= hi, f"{rate:.2f} Hz > {hi} Hz"


def test_read_is_fast(module, dut):
    _streaming(module)
    dut.collect(1.0)
    times = []
    for _ in range(20):
        dut.advance(0.02)
        t = time.perf_counter()
        dut.inst.read()
        times.append((time.perf_counter() - t) * 1000)
    assert statistics.median(times) < 5.0, f"read() median {statistics.median(times):.2f} ms (limit 5 ms)"
    assert sorted(times)[-3] < 5.0 * 4, "read() p85 should also stay well below 20 ms"


def test_read_drains(module, dut):
    _streaming(module)
    dut.collect(1.0)
    first = dut.inst.read()
    second = dut.inst.read()
    assert not (first and second and first[-1] is second[-1]), "read() must drain, not re-return samples"
    assert len(second) <= max(1, len(first))


def test_health_is_honest(module, dut):
    h0 = dut.inst.health()
    assert h0.ok is True, f"health not ok right after start: {h0.note}"
    assert h0.error_count >= 0
    if module in STREAMING:
        dut.collect(min(WINDOW_S[module], 5.0) if module != "env" else 6.0)
        h = dut.inst.health()
        assert h.ok and h.last_sample_age_ms is not None
        assert h.last_sample_age_ms < STALE_AFTER_MS[module], "age must reflect real sample recency"
    dut.inst.stop()
    assert dut.inst.health().ok is False, "health().ok must be False after stop()"


def test_no_exception_escapes(module, make_dut):
    h = make_dut()
    inst = h.inst
    if module in STREAMING:
        assert inst.read() == []  # before start()
    inst.start()
    inst.start()
    if module in STREAMING:
        h.collect(0.5)
    if module == "haptics":
        for motor, level in (("top", 0.5), ("back", 1.0), ("top", -3.0), ("back", 7.0), ("left", 0.5)):
            inst.set(motor, level)  # clamps or logs; must not raise
        inst.off_all()
    inst.stop()
    inst.stop()
    if module in STREAMING:
        assert inst.read() == [] or isinstance(inst.read(), list)  # after stop()


def test_haptics_set_and_off(module, dut):
    if module != "haptics":
        pytest.skip("haptics only")
    dut.inst.set("top", 1.0)
    dut.inst.set("back", 0.5)
    dut.inst.off_all()
    assert dut.inst.health().ok


def test_camera_saves_real_clip(module, dut, tmp_path):
    if module != "camera":
        pytest.skip("camera only")
    if not dut.inst.health().ok and "ffmpeg" in dut.inst.health().note:
        pytest.skip("ffmpeg not installed")
    dut.advance(6.0)
    now = dut.clock.now_ns()
    out = tmp_path / "clip.mp4"
    info = dut.inst.save_clip(now - 4_000_000_000, now, str(out))
    assert out.exists() and out.stat().st_size > 1000 and info.bytes == out.stat().st_size
    assert info.path == str(out) and info.camera == "front"
    assert 2.0 <= info.duration_s <= 6.0
    assert info.t_end_ns > info.t_start_ns
    assert out.read_bytes()[4:8] == b"ftyp", "not an MP4"
