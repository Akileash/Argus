import sys
import time
import types

import pytest
import yaml

from argus.bus import SampleBus
from argus.config import DEFAULT_CONFIG_PATH, parse_config
from argus.contracts import ModuleContext, MonotonicClock
from argus.supervisor import ModuleLoadError, Supervisor, load_impl, shadow
from hardware.mock.clock import VirtualClock


def make_cfg(fallback: str | None = "mock", **overrides):
    raw = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text())
    for m in raw["modules"].values():
        m["fallback"] = fallback
        m.setdefault("options", {})
    raw["modules"]["camera"]["options"]["cache_dir"] = str(DEFAULT_CONFIG_PATH.parent / "var" / "mock_camera_test")
    for k, v in overrides.items():
        raw["modules"][k].update(v)
    return parse_config(raw)


@pytest.fixture
def sup():
    clock = VirtualClock()
    events: list[dict] = []
    s = Supervisor(make_cfg(), SampleBus(), clock, on_event=events.append, scenario="idle")
    s.events = events  # type: ignore[attr-defined]
    s.vclock = clock  # type: ignore[attr-defined]
    s.start(run_threads=False)
    yield s
    s.stop()


def pump(s: Supervisor, seconds: float, step: float = 0.05) -> None:
    for _ in range(round(seconds / step)):
        s.vclock.advance(step)
        for n in ("imu", "range", "hat_on", "env"):
            s.poll_once(n)
            s.check_once(n)


def test_modules_feed_the_bus(sup):
    pump(sup, 1.0)
    assert len(sup.bus.window("imu", 0, 1 << 62)) > 380
    assert len(sup.bus.window("range", 0, 1 << 62)) > 30
    assert len(sup.bus.window("tof", 0, 1 << 62)) >= 9
    assert sup.events == []
    assert all(v["impl"] == "mock" for v in sup.status().values())


def test_dead_module_falls_back_and_records_event(sup):
    pump(sup, 0.5)
    old = sup.module("imu")
    old.kill()
    sup.check_once("imu")
    assert sup.module("imu") is not old
    assert sup.status()["imu"]["on_fallback"] is True
    ev = sup.events[0]
    assert ev["type"] == "module_fallback" and ev["module"] == "imu"
    assert ev["from_impl"] == "mock" and ev["to_impl"] == "mock" and "health" in ev["reason"]
    n = len(sup.bus.window("imu", 0, 1 << 62))
    pump(sup, 0.5)
    assert len(sup.bus.window("imu", 0, 1 << 62)) > n + 150  # samples keep flowing after fallback


def test_stalled_module_detected_by_staleness_not_health(sup):
    pump(sup, 0.5)
    sup.module("imu").stall()
    assert sup.module("imu").health().ok  # it lies: health looks fine
    pump(sup, 0.4)
    assert sup.events == []  # 400 ms < 500 ms limit
    pump(sup, 0.2)
    assert sup.events and "stale" in sup.events[0]["reason"] and sup.events[0]["module"] == "imu"


def test_hat_on_tolerates_gap_up_to_3s(sup):
    pump(sup, 0.5)
    sup.module("hat_on").stall()
    pump(sup, 2.5)
    assert not [e for e in sup.events if e["module"] == "hat_on"]
    pump(sup, 1.0)
    assert [e for e in sup.events if e["module"] == "hat_on"]


def test_fallback_happens_within_one_second_in_real_time():
    s = Supervisor(make_cfg(), SampleBus(), MonotonicClock(), scenario="idle")
    s.start()
    try:
        time.sleep(0.3)
        t0 = time.monotonic()
        s.module("imu").kill()
        while not s.status()["imu"]["on_fallback"] and time.monotonic() - t0 < 2:
            time.sleep(0.01)
        assert time.monotonic() - t0 <= 1.0
        n = len(s.bus.window("imu", 0, 1 << 62))
        time.sleep(0.3)
        assert len(s.bus.window("imu", 0, 1 << 62)) > n
    finally:
        s.stop()


def test_no_fallback_configured_gives_up_cleanly():
    events: list[dict] = []
    clock = VirtualClock()
    s = Supervisor(make_cfg(fallback=None), SampleBus(), clock, on_event=events.append)
    s.start(run_threads=False)
    s.module("range").kill()
    s.check_once("range")
    s.check_once("range")  # no repeated events
    assert len(events) == 1 and events[0]["to_impl"] is None
    assert s.status()["range"]["gave_up"] is True
    assert s.status()["imu"]["gave_up"] is False
    s.stop()


def test_missing_fallback_impl_is_reported_not_fatal():
    events: list[dict] = []
    s = Supervisor(make_cfg(fallback="team"), SampleBus(), VirtualClock(), on_event=events.append)
    s.start(run_threads=False)
    s.module("imu").kill()
    s.check_once("imu")
    assert events[0]["to_impl"] is None and "not pasted yet" in events[0]["reason"]
    s.poll_once("range")  # other modules unaffected
    s.stop()


def test_failing_primary_impl_at_start_uses_fallback():
    events: list[dict] = []
    s = Supervisor(make_cfg(imu={"impl": "team", "fallback": "mock"}), SampleBus(), VirtualClock(),
                   on_event=events.append)
    s.start(run_threads=False)
    assert s.status()["imu"]["impl"] == "mock" and s.status()["imu"]["on_fallback"]
    assert events[0]["from_impl"] == "team"
    s.stop()


def test_haptics_selftest_failure_triggers_fallback(sup):
    sup.check_once("haptics")
    assert sup.events == []
    sup.module("haptics").kill()
    sup.check_once("haptics")
    assert sup.events and sup.events[0]["module"] == "haptics"


def test_haptics_proxy_survives_driver_crash_mid_buzz(sup):
    sup.haptics.set("back", 1.0)
    sup.module("haptics").kill()
    sup.haptics.set("top", 0.7)  # raises inside driver -> fallback -> retried on the new driver
    assert sup.events and sup.events[0]["module"] == "haptics"
    assert sup.module("haptics").state["top"] == 0.7
    assert sup.haptics.busy


def test_haptics_selftest_skipped_while_buzzing(sup):
    sup.haptics.set("top", 1.0)
    sup.check_once("haptics")
    assert sup.module("haptics").state["top"] == 1.0  # the zero-intensity ping must not cancel a pattern


def test_reload_swaps_one_module_fast_without_touching_others(sup):
    pump(sup, 0.5)
    others = {n: sup.module(n) for n in ("range", "hat_on", "env", "haptics", "camera")}
    old = sup.module("imu")
    dt = sup.reload("imu")
    assert dt < 2.0
    assert sup.module("imu") is not old
    assert old.health().ok is False  # old impl stopped
    assert all(sup.module(n) is inst for n, inst in others.items())
    pump(sup, 0.3)
    assert sup.events == []


def test_reload_rereads_config_file(tmp_path):
    raw = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text())
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump(raw))
    s = Supervisor(make_cfg(), SampleBus(), VirtualClock(), config_path=path)
    s.start(run_threads=False)
    raw["modules"]["env"] = {"impl": "mock", "fallback": "replay", "options": {"x": 1}}
    path.write_text(yaml.safe_dump(raw))
    s.reload("env")
    assert s.status()["env"]["fallback_cfg"] == "replay"
    s.stop()


def test_reload_to_broken_impl_keeps_old_running(sup):
    old = sup.module("imu")
    with pytest.raises(ModuleLoadError):
        sup.reload("imu", impl="team")
    assert sup.module("imu") is old and old.health().ok


def test_reload_clears_fallback_state(sup):
    sup.module("imu").kill()
    sup.check_once("imu")
    assert sup.status()["imu"]["on_fallback"]
    sup.reload("imu")
    assert not sup.status()["imu"]["on_fallback"]


def test_load_impl_rejects_objects_that_break_the_protocol():
    pkg = types.ModuleType("hardware.broken")
    mod = types.ModuleType("hardware.broken.imu")
    mod.create = lambda ctx: object()  # type: ignore[attr-defined]
    sys.modules["hardware.broken"], sys.modules["hardware.broken.imu"] = pkg, mod
    try:
        with pytest.raises(ModuleLoadError, match="does not satisfy Imu"):
            load_impl("imu", "broken", ModuleContext(VirtualClock()))
    finally:
        del sys.modules["hardware.broken"], sys.modules["hardware.broken.imu"]


def test_load_impl_errors_are_clear():
    ctx = ModuleContext(VirtualClock())
    with pytest.raises(ModuleLoadError, match="cannot import"):
        load_impl("imu", "nonexistent", ctx)
    with pytest.raises(ModuleLoadError, match="unknown module"):
        load_impl("lidar", "mock", ctx)


def test_shadow_writes_diff_log(tmp_path):
    clock = VirtualClock()
    p = shadow("imu", "mock", "mock", 2.0, clock=clock, scenario="walk_forward", out_dir=tmp_path)
    lines = [__import__("json").loads(x) for x in p.read_text().splitlines()]
    summ = lines[-1]["summary"]
    assert summ["module"] == "imu" and sum(summ["count"].values()) == len([x for x in lines if "impl" in x])
    assert summ["diff"]["ax"]["mean_abs_diff"] == 0.0 and summ["diff"]["ax"]["matched"] > 500
    assert any(x.get("impl") == "mock" for x in lines[:-1])


def test_shadow_alternate_mode_and_exclusive_modules(tmp_path):
    p = shadow("range", "mock", "mock", 3.0, clock=VirtualClock(), out_dir=tmp_path, alternate=True)
    summ = __import__("json").loads(p.read_text().splitlines()[-1])["summary"]
    assert summ["alternate"] and summ["diff"] == {}
    with pytest.raises(ValueError, match="exclusive-access"):
        shadow("haptics", "mock", "mock", 1.0)
    with pytest.raises(ValueError, match="exclusive-access"):
        shadow("camera", "mock", "mock", 1.0)
