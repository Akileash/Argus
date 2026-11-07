"""The declarative adapters themselves: restart with backoff, error counting, python source, haptics, camera."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from argus.contracts import ModuleContext, MonotonicClock
from hardware.team._declarative import create

from .conftest import FAKES, MAPPINGS
from .test_live_swap import wait_for

ADIR = Path(__file__).parents[2] / "hardware" / "team"


def adapter(module, mapping=None, **options):
    # always an explicit file, so a teammate's committed mapping.yaml never leaks into these tests
    opts = {"mapping_file": str(MAPPINGS / f"{mapping or 'empty'}.yaml")}
    return create(module, ModuleContext(MonotonicClock(), {**opts, **options}), ADIR / module)


def test_crashed_script_is_restarted_with_backoff_and_health_says_why():
    a = adapter("imu", "imu", command=["python", str(FAKES / "fake_imu.py"), "--crash-after", "30"],
                restart={"initial_s": 0.15, "max_s": 0.3})
    a.start()
    try:
        h = wait_for(lambda: (h := a.health()) if not a.health().ok else None, what="down health")
        assert "exited with code 3" in h.note and "restarting in 0.2 s" in h.note or "restarting in 0.1" in h.note
        assert "IMU bus error" in h.note, "last stderr line should reach the health note"
        assert h.error_count >= 1
        first = a.read()
        assert first, "samples before the crash are kept"
        wait_for(lambda: a.health().ok and a.read(), what="recovery after restart")
    finally:
        a.stop()
    assert not a.health().ok


def test_backoff_grows_when_the_script_never_works():
    a = adapter("range", "range", command=["python", str(FAKES / "fake_range.py"), "--exit-now"],
                restart={"initial_s": 0.1, "max_s": 0.4})
    a.start()
    try:
        wait_for(lambda: a.health().error_count >= 4, timeout=6, what="4 restarts")
        assert "right after start, with no output" in a.health().note
    finally:
        a.stop()


def test_missing_command_is_a_health_problem_not_a_crash():
    a = adapter("imu", "imu", command=["/no/such/binary"])
    a.start()
    try:
        h = wait_for(lambda: (h := a.health()) if not a.health().ok else None, what="down health")
        assert "FileNotFoundError" in h.note
    finally:
        a.stop()


def test_malformed_lines_counted_and_skipped_samples_still_flow(tmp_path):
    script = tmp_path / "noisy.py"
    script.write_text("import sys, time\nfor i in range(40):\n"
                      "    print('R2=100' if i % 2 else 'Traceback junk', flush=True)\ntime.sleep(5)\n")
    a = adapter("range", format="text", command=["python", str(script)], sensors={"R2": "R2"}, unit="mm")
    a.start()
    try:
        got = wait_for(lambda: a.read() if a.health().error_count >= 20 else None, what="20 bad lines")
        h = a.health()
        assert h.ok and h.error_count == 20 and "bad line" in h.note
        assert all(s.range_mm == 100.0 for s in got) and len(got) >= 15
    finally:
        a.stop()


def test_warmup_drops_early_output_without_counting_errors(tmp_path):
    script = tmp_path / "w.py"
    script.write_text("import time\nwhile True:\n    print('junk')\n    print('R2=5', flush=True)\n    time.sleep(0.05)\n")
    a = adapter("range", format="text", command=["python", str(script)], sensors={"R2": "R2"}, warmup_s=0.5)
    a.start()
    try:
        time.sleep(0.4)
        assert a.read() == [] and a.health().error_count == 0
        wait_for(lambda: a.read(), what="data after warmup")
    finally:
        a.stop()


def test_python_source_import_and_call_with_transient_errors():
    a = adapter("range", python="tests.team.fakes.py_source:read_once", setup="setup", interval_s=0.01,
                sensors={"R2": "R.0", "R3": "R.1"}, unit="cm")
    a.start()
    try:
        got = wait_for(lambda: a.read() if a.health().error_count >= 2 else None, what="2 transient errors")
        assert a.health().ok, "single errors from their function are counted, not fatal"
        assert {s.sensor_id for s in got} == {"R2", "R3"} and got[0].range_mm == pytest.approx(1480.0)
    finally:
        a.stop()
    from tests.team.fakes import py_source

    assert py_source.SETUP_CALLS


def test_python_generator_source():
    a = adapter("range", python="tests.team.fakes.py_source:stream", format="text", unit="mm",
                sensors={"R2": "R2", "R3": "R3"})
    a.start()
    try:
        got = wait_for(lambda: a.read(), what="generator output")
        assert got[0].sensor_id == "R2" and any(s.sensor_id == "R3" and s.range_mm is None for s in got)
    finally:
        a.stop()


def test_python_source_import_error_fails_health_not_the_process():
    a = adapter("imu", "imu", command=None, python="no_such_module_xyz:go")
    a.start()
    try:
        h = wait_for(lambda: (h := a.health()) if not a.health().ok else None, what="down health")
        assert "ModuleNotFoundError" in h.note
    finally:
        a.stop()


def test_unconfigured_team_modules_still_raise_not_pasted():
    for m in ("imu", "range", "hat_on", "env", "haptics", "camera"):
        with pytest.raises(NotImplementedError, match=f"team impl for {m} not pasted yet"):
            create(m, ModuleContext(MonotonicClock(), {"mapping_file": str(MAPPINGS / "empty.yaml")}), ADIR / m)


# ---------------------------------------------------------------- haptics
def haptics(tmp_path, monkeypatch, mapping="haptics", **options):
    monkeypatch.setenv("ARGUS_FAKE_HAPTICS_LOG", str(tmp_path / "log.txt"))
    h = adapter("haptics", mapping, **options)
    h.start()
    return h, tmp_path / "log.txt"


def test_haptics_stdin_lines(tmp_path, monkeypatch):
    h, log = haptics(tmp_path, monkeypatch)
    h.set("top", 1.0)
    h.set("back", 0.5)
    h.set("top", 7.0)  # clamped
    h.set("left", 0.5)  # unknown motor: counted, not raised
    h.off_all()
    wait_for(lambda: log.exists() and len(log.read_text().splitlines()) >= 5, what="lines at the script")
    assert log.read_text().splitlines()[:4] == ["H,top,255", "H,back,128", "H,top,255", "H,top,0"]
    assert h.health().ok and h.health().error_count == 1
    h.stop()
    assert not h.health().ok


def test_haptics_script_dying_is_reported(tmp_path, monkeypatch):
    h, _ = haptics(tmp_path, monkeypatch, command=["python", "-c", "import sys; sys.exit(7)"])
    time.sleep(0.5)
    for _ in range(3):
        h.set("top", 1.0)  # writes to a dead pipe: must not raise
    hl = h.health()
    assert not hl.ok and "exited with code 7" in hl.note
    h.stop()


def test_haptics_python_function_and_scale(tmp_path):
    calls = []
    sys.modules["fake_motor_lib"] = type(sys)("fake_motor_lib")
    sys.modules["fake_motor_lib"].set_duty = lambda pin, pct: calls.append((pin, pct))
    h = adapter("haptics", python="fake_motor_lib", call="set_duty", motors={"top": 18, "back": 19}, scale=100)
    h.start()
    h.set("back", 0.25)
    h.set("top", 2.0)
    assert calls == [(19, 25.0), (18, 100.0)] and h.health().ok
    h.stop()
    sys.modules["fake_motor_lib"].set_duty = lambda *_: 1 / 0  # their function raises
    h = adapter("haptics", python="fake_motor_lib", call="set_duty")
    h.start()
    h.set("top", 1.0)
    assert not h.health().ok and "ZeroDivisionError" in h.health().note
    h.stop()


# ---------------------------------------------------------------- camera
def test_camera_save_command(tmp_path):
    cam = adapter("camera", save_command=["python", "-c", "import sys; open(sys.argv[1],'wb').write(b'x'*5000)",
                                          "{path}"])
    cam.start()
    out = tmp_path / "c.mp4"
    t = MonotonicClock().now_ns()
    info = cam.save_clip(t - 4_000_000_000, t, str(out))
    assert out.stat().st_size == 5000 and info.bytes == 5000 and info.duration_s == pytest.approx(4.0)
    assert cam.health().ok
    bad = adapter("camera", save_command=["python", "-c", "raise SystemExit(3)"])
    bad.start()
    with pytest.raises(RuntimeError, match="exited 3"):
        bad.save_clip(t - 1, t, str(tmp_path / "x.mp4"))
    assert bad.health().error_count == 1
