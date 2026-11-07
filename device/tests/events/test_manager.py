"""Event manager: merge, cooldown, upgrade, hat_removed log-only, schema validity, queue atomicity."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from argus.bus import SampleBus
from argus.config import load_config
from argus.contracts import EnvSample, ImuSample
from argus.detectors.base import Detection
from argus.events.manager import EventManager, mergeable
from argus.events.queue import EventQueue, SchemaError, validate_event, validate_trace
from argus.events.synthetic import TRIGGER_TYPES, synthesize
from argus.events.trace import TraceRecorder, build_trace
from tests.e2e.harness import StubCamera

S = 1_000_000_000
BASE = 100 * S


class Env:
    def __init__(self, tmp_path, hat_on=True):
        self.cfg = load_config()
        self.now = BASE
        self.hat_on = hat_on
        self.bus = SampleBus(35)
        self.rec = TraceRecorder()
        self.cam = StubCamera()
        self.queue = EventQueue(tmp_path / "q", 2048)
        self.mgr = EventManager(self.cfg, self.bus, self.cam, self.queue, self.rec, now_fn=lambda: self.now,
                                hat_on_fn=lambda: self.hat_on,
                                impls_fn=lambda: ({"imu": "mock"}, ["range"]),
                                wall_fn=lambda: datetime(2026, 1, 1, tzinfo=UTC))

    def at(self, t_s):
        self.now = BASE + int(t_s * S)
        self.mgr.tick(self.now)

    def det(self, type_, t_s, sensor_id=None, **kw):
        return Detection(BASE + int(t_s * S), type_, sensor_id, **kw)

    def events(self):
        return [json.loads((d / "event.json").read_text()) for d in self.queue.ready()]


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def test_event_waits_for_post_roll_then_writes(env):
    eid = env.mgr.on_detection(env.det("fall", 0, peak_g=12.0, free_fall_ms=450.0), env.now)
    env.at(9.9)
    assert env.events() == []
    env.at(10.0)
    [ev] = env.events()
    assert ev["event_id"] == eid and ev["type"] == "fall" and ev["device_class"] == "incident"
    assert ev["window"] == {"pre_s": 20.0, "post_s": 10.0}
    assert [m["kind"] for m in ev["media"]] == ["clip", "sensor_trace"]
    assert ev["firmware"] == {"version": "0.1.0", "impls": {"imu": "mock"}, "fallbacks_active": ["range"]}
    assert ev["metrics"]["peak_g"] == 12.0 and ev["metrics"]["hat_on"] is True
    assert env.cam.calls == [(BASE - 20 * S, BASE + 10 * S)]


def test_merge_same_type_extends_up_to_cap_and_is_recorded(env):
    env.mgr.on_detection(env.det("head_impact", 0, peak_g=7.0), env.now)
    env.at(4)
    assert env.mgr.on_detection(env.det("head_impact", 4, peak_g=8.0), env.now) == env.mgr.open_events()[0].event_id
    assert env.mgr.open_events()[0].end_ns == BASE + 14 * S
    env.at(12)
    env.mgr.on_detection(env.det("head_impact", 12, peak_g=8.0), env.now)
    assert env.mgr.open_events()[0].end_ns == BASE + 20 * S  # capped at original end + 10 s
    env.at(20)
    [ev] = env.events()
    assert ev["window"]["post_s"] == 20.0
    assert ev["merged_triggers"] == [{"type": "head_impact", "offset_s": 4.0}, {"type": "head_impact", "offset_s": 12.0}]


def test_cooldown_blocks_same_type_for_5s_after_close(env):
    env.mgr.on_detection(env.det("fall", 0), env.now)
    env.at(10)
    env.at(14.9)
    assert env.mgr.on_detection(env.det("fall", 14.9), env.now) is None
    assert env.mgr.dropped == [("fall", "cooldown")]
    env.at(15.1)
    assert env.mgr.on_detection(env.det("fall", 15.1), env.now) is not None
    # a different type is not affected
    assert env.mgr.on_detection(env.det("head_impact", 15.1), env.now) is not None


def test_near_miss_upgraded_to_contact_not_second_event(env):
    env.mgr.on_detection(env.det("struck_by_near_miss", 0, "R1", closing_rate_mps=2.5, ttc_s=0.6, range_m=1.2), env.now)
    env.at(1.0)
    env.mgr.on_detection(env.det("struck_by_contact", 1.0, "R1", peak_g=9.0, closing_rate_mps=2.5), env.now)
    env.mgr.on_detection(env.det("head_impact", 1.0, peak_g=9.0), env.now)  # same blow, merged
    assert len(env.mgr.open_events()) == 1
    env.at(11)
    [ev] = env.events()
    assert ev["type"] == "struck_by_contact" and ev["device_class"] == "incident"
    assert ev["metrics"]["peak_g"] == 9.0 and ev["metrics"]["sensor_id"] == "R1"
    assert ev["metrics"]["ttc_s"] == 0.6
    assert [m["type"] for m in ev["merged_triggers"]] == ["head_impact"]
    assert ev["window"]["post_s"] == 11.0  # contact extended the post-roll


def test_mergeable_table():
    assert mergeable("fall", "fall") and mergeable("struck_by_near_miss", "head_impact")
    assert not mergeable("head_impact", "hat_knocked_off") and not mergeable("fall", "head_impact")


def test_warning_and_hat_on_are_not_events(env):
    assert env.mgr.on_detection(env.det("struck_by_warning", 0, "R1"), env.now) is None
    assert env.mgr.on_detection(env.det("hat_on", 0), env.now) is None
    assert env.mgr.open_events() == [] and env.events() == []


def test_hat_removed_is_event_json_only(tmp_path):
    env = Env(tmp_path, hat_on=False)
    d = Detection(BASE + 8 * S, "hat_removed", debug={"off_start_ns": BASE + 5 * S})
    env.now = BASE + 8 * S
    env.mgr.on_detection(d, env.now)
    [ev] = env.events()
    assert ev["type"] == "hat_removed" and ev["device_class"] == "ppe_log" and ev["media"] == []
    assert ev["metrics"]["hat_on"] is False and ev["window"] == {"pre_s": 0.0, "post_s": 0.0}
    assert sorted(p.name for p in (env.queue.root / ev["event_id"]).iterdir()) == ["READY", "event.json"]
    assert env.cam.calls == []


def test_module_fallback_event(env):
    env.mgr.on_fallback({"type": "module_fallback", "module": "imu", "from_impl": "argus", "to_impl": "team",
                         "reason": "stale", "t_ns": env.now})
    [ev] = env.events()
    assert ev["type"] == "module_fallback" and ev["device_class"] == "system" and ev["media"] == []
    assert "imu" in ev["firmware"]["fallbacks_active"]


def test_camera_failure_still_queues_event_with_trace(env):
    def boom(*a):
        raise RuntimeError("no video")

    env.cam.save_clip = boom
    env.mgr.on_detection(env.det("fall", 0), env.now)
    env.at(10)
    [ev] = env.events()
    assert [m["kind"] for m in ev["media"]] == ["sensor_trace"]


def test_flush_truncates_post_roll(env):
    env.mgr.on_detection(env.det("fall", 0), env.now)
    env.at(3)
    env.mgr.flush(env.now)
    [ev] = env.events()
    assert ev["window"]["post_s"] == 3.0


def test_window_shrinks_to_bus_history(env):
    env.mgr.on_detection(env.det("fall", 0), env.now)
    env.at(5)
    env.mgr.on_detection(env.det("fall", 5), env.now)
    env.at(15)
    env.mgr.on_detection(env.det("fall", 15), env.now)  # extends to +20 s: 40 s window > 35 s of history
    env.at(20)
    [ev] = env.events()
    assert ev["window"]["pre_s"] + ev["window"]["post_s"] <= 35 - 1.5 + 1e-6


@pytest.mark.parametrize("t", TRIGGER_TYPES)
def test_synthetic_events_are_schema_valid(env, t):
    env.mgr.on_detection(synthesize(t, env.now), env.now)
    env.at(11)
    [ev] = env.events()
    validate_event(ev)
    assert ev["type"] == t


def test_trace_is_schema_valid_and_keeps_peaks(env):
    for i in range(3000):  # 3 s at 1 kHz around t0, one 12 g spike
        t = BASE + i * 1_000_000
        env.bus.publish("imu", [ImuSample(t, 12.0 if i == 1500 else 0.0, 0.0, 1.0, 1.0, 2.0, 3.0)])
    env.bus.publish("env", [EnvSample(BASE, 600.0, 22.0, 40.0)])
    tr = build_trace("11111111-1111-4111-8111-111111111111", BASE + 1500 * 1_000_000, BASE, BASE + 3 * S, env.bus,
                     env.rec)
    validate_trace(tr)
    assert len(tr["imu"]["t"]) == 300  # 100 Hz
    assert max(tr["imu"]["a_mag_g"]) > 11 and tr["imu_peaks"][0]["a_mag_g"] > 11
    assert tr["env"]["co2_ppm"] == 600.0 and tr["hat_on"] == [{"t": -1.5, "on": True}]


def test_invalid_event_never_becomes_visible(env):
    bad = {"schema_version": "1", "event_id": "x"}
    tmp = env.queue.tmp_dir("x")
    with pytest.raises(SchemaError):
        env.queue.commit(bad, tmp)
    assert env.queue.ready() == [] and [p.name for p in env.queue.root.iterdir()] == []


def test_partial_event_without_ready_is_ignored(env):
    env.queue.tmp_dir("abc")
    (env.queue.root / "def").mkdir()
    (env.queue.root / "def" / "event.json").write_text("{}")
    assert env.queue.ready() == []


def test_disk_guard_drops_oldest_clip_keeps_rows(tmp_path):
    cfg = load_config()
    env = Env(tmp_path)
    env.queue.max_bytes = 1_500
    env.mgr.cam = None
    big = b"x" * 1000
    ids = []
    for i in range(3):
        env.cam.save_clip = lambda a, b, p, _big=big: (Path(p).write_bytes(_big), StubCamera().save_clip(a, b, p))[1]
        env.now = BASE + i * 100 * S
        ids.append(env.mgr.on_detection(env.det("fall", 100 * i), env.now))
        env.at(100 * i + 10)
    _ = cfg
    for eid in ids:
        assert (env.queue.root / eid / "event.json").exists()
    states = [(env.queue.root / e / "upload_state.json") for e in ids]
    assert states[0].exists() and json.loads(states[0].read_text())["clip_dropped"] is True
    assert not (env.queue.root / ids[0] / "clip.mp4").exists()
