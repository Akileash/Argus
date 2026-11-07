"""Every scenario YAML through mocks -> pipeline on a virtual clock; the queue must hold exactly the declared
events (type, sensor, time window). Spec §4, §5.6."""

from __future__ import annotations

import hashlib
import json
import shutil

import pytest

from hardware.mock.camera import find_ffmpeg
from hardware.mock.scenario import list_scenarios, load_scenario
from tests.e2e.harness import make_run

SCENARIOS = list_scenarios()


def _run(name, tmp_path, **kw):
    sc = load_scenario(name)
    r = make_run(name, tmp_path / "q", **kw)
    r.advance(sc.duration_s + 12)  # let the 10 s post-roll complete
    r.pipe.manager.flush()
    return sc, r


@pytest.mark.parametrize("name", SCENARIOS)
def test_queue_holds_exactly_the_expected_events(name, tmp_path):
    sc, r = _run(name, tmp_path)
    events = r.events()
    got = sorted(e["type"] for e in events)
    want = sorted(e.type for e in sc.expected)
    assert got == want, f"{name}: queue {got} != expected {want}"
    unexpected = {d.type for d in r.pipe.detections} - {e.type for e in sc.expected} - set(sc.spec.allowed_extra)
    assert not unexpected, f"{name}: detections outside expected/allowed_extra: {unexpected}"
    for exp in sc.expected:
        ev = next(e for e in events if e["type"] == exp.type)
        lo, hi = exp.t_window_s
        assert lo <= r.t_s(ev["t0"]) <= hi, f"{name}: {exp.type} t0={r.t_s(ev['t0']):.2f} outside {exp.t_window_s}"
        if exp.sensors:
            assert ev["metrics"]["sensor_id"] in exp.sensors
        assert ev["device_serial"]
        assert ev["firmware"]["impls"]["imu"] == "mock"


@pytest.mark.parametrize("name", [n for n in SCENARIOS if load_scenario(n).expected])
def test_files_match_event_json(name, tmp_path):
    _, r = _run(name, tmp_path)
    for d in r.pipe.queue.ready():
        ev = json.loads((d / "event.json").read_text())
        assert (d / "READY").exists()
        names = sorted(p.name for p in d.iterdir())
        if ev["type"] == "hat_removed":
            assert ev["media"] == [] and names == ["READY", "event.json"]
            assert ev["device_class"] == "ppe_log" and ev["window"] == {"pre_s": 0.0, "post_s": 0.0}
            assert ev["metrics"]["hat_on"] is False
            continue
        assert names == ["READY", "clip.mp4", "event.json", "trace.json"]
        for m in ev["media"]:
            data = (d / m["filename"]).read_bytes()
            assert m["bytes"] == len(data) and m["sha256"] == hashlib.sha256(data).hexdigest()
        trace = json.loads((d / "trace.json").read_text())
        assert trace["event_id"] == ev["event_id"] and trace["imu"]["t"]
        assert trace["t_start_s"] <= -ev["window"]["pre_s"] + 1e-3 and trace["t_end_s"] >= ev["window"]["post_s"] - 1e-3


def test_board_swing_trace_and_haptics(tmp_path):
    _, r = _run("board_swing_rear", tmp_path)
    ev = r.events()[0]
    assert ev["type"] == "struck_by_near_miss" and ev["device_class"] == "near_miss"
    assert ev["metrics"]["closing_rate_mps"] > 1.5 and ev["metrics"]["ttc_s"] < 1.3
    trace = json.loads((r.pipe.queue.ready()[0] / "trace.json").read_text())
    assert any(h["motor"] == "back" for h in trace["haptics"])
    assert any(d["type"] == "struck_by_warning" for d in trace["detections"])
    closing = [v for s in trace["ranges"].values() for v in s["closing_mps"] if v is not None]
    assert closing and max(closing) > 1.5


def test_hat_drop_buzzes_both_motors(tmp_path):
    _, r = _run("hat_drop_1m", tmp_path)
    mock = r.sup.module("haptics")
    assert {c.motor for c in mock.calls if c.intensity == 1.0} == {"top", "back"}
    assert r.events()[0]["metrics"]["free_fall_ms"] > 400 and r.events()[0]["metrics"]["peak_g"] > 6


def test_real_ffmpeg_clip(tmp_path):
    """Real mock-camera clip. Triggered at t=25 s: the mock camera cannot cut a window starting at scenario
    t=0 (known mock bug: ffmpeg yields one segment too few), so the pre-roll must start after 0."""
    if not (find_ffmpeg() or shutil.which("ffmpeg")):
        pytest.skip("ffmpeg not installed")
    from argus.events.synthetic import synthesize

    r = make_run("idle", tmp_path / "q", stub_camera=False)
    r.advance(25)
    r.pipe.inject(synthesize("fall", r.clock.now_ns()))
    r.advance(10.5)
    d = r.pipe.queue.ready()[0]
    ev = json.loads((d / "event.json").read_text())
    assert (d / "clip.mp4").stat().st_size > 1000
    clip = next(m for m in ev["media"] if m["kind"] == "clip")
    assert clip["duration_s"] >= 29 and clip["bytes"] == (d / "clip.mp4").stat().st_size


def test_boot_prompts_and_led_follow_calibration(tmp_path):
    from argus.status_led import StatusLed  # noqa: F401  (type of the hook)

    class FakeLed:
        def __init__(self):
            self.modes = []

        def set_mode(self, m):
            self.modes.append(m)

        def close(self):
            pass

    run = make_run("idle", tmp_path)
    led = FakeLed()
    run.pipe.led = led
    run.pipe.boot_phase = ""
    run.pipe._update_phase()
    assert run.pipe.boot_phase == "calibrating" and led.modes == ["solid"]
    run.advance(8.0)
    assert run.pipe.boot_phase == "ready"
    assert led.modes[0] == "solid" and led.modes[-1] == "off"
    assert set(led.modes) <= {"solid", "blink", "off"}
