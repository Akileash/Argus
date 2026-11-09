"""Ultrasonic near-miss rule: synthetic scenarios with the noise model from the 2026-11-08 logs."""

from __future__ import annotations

import pytest

import math

from hat import config as cfg
from hat.tools.replay_us import LegacyDetector, evaluate, run, scenarios
from hat.ultrasonic import ApproachDetector, MotionState

SC = {s.name: s for s in scenarios()}
SEEDS = 12


def new():
    return ApproachDetector("x")


def _by(prefix: str):
    return [s for n, s in SC.items() if n.startswith(prefix)]


def test_old_rule_really_false_fires_on_static_wall():
    # regression anchor: the model reproduces the field problem (logged: ~16/min/sensor)
    rate, total, _ = evaluate(SC["static wall 0.78 m, standing"], LegacyDetector, 2, use_motion=False)
    assert total / 2 / 120 * 60 > 8


@pytest.mark.parametrize("prefix", ["static wall", "walk BACKWARD", "head turn", "person walking ACROSS", "hand approach"])
def test_quiet_scenarios_do_not_fire(prefix):
    for sc in _by(prefix):
        fire_rate, total, _ = evaluate(sc, new, SEEDS)
        assert total == 0, (sc.name, total)


def test_person_walking_toward_wearer_fires_with_warning_time():
    fire_rate, _, lead = evaluate(SC["person walking TOWARD wearer from behind 1.4 m/s"], new, SEEDS)
    assert fire_rate == 1.0 and lead >= 0.8


@pytest.mark.parametrize("name,min_rate,min_lead", [
    ("object swung at head 3.0 m/s, wearer standing", 0.9, 0.4),
    ("object swung at head 4.5 m/s, wearer standing", 0.8, 0.3),
    ("object swung at head 6.0 m/s, wearer standing", 0.7, 0.2),
    ("object swung at head 4.5 m/s, wearer walking", 0.8, 0.3),
])
def test_swung_object_still_fires_in_useful_time(name, min_rate, min_lead):
    rate, _, lead = evaluate(SC[name], new, 30)
    assert rate >= min_rate and lead >= min_lead


def _feed(det, samples, motion=None, t0=10.0, dt=0.08):
    out = []
    for i, r in enumerate(samples):
        ev = det.update(t0 + i * dt, r, motion)
        if ev:
            out.append(ev)
    return out


def test_single_spike_does_not_fire():
    assert _feed(new(), [0.78, 0.78, 0.74, 0.78, 0.78, 0.78]) == []
    assert _feed(new(), [0.78, 0.78, 0.62, 0.78, 0.78, 0.78]) == []


def test_clean_one_mps_approach_fires_within_ttc():
    ev = _feed(new(), [1.70, 1.62, 1.54, 1.46, 1.38, 1.30])
    assert ev and ev[0].reason == "close_and_fast" and ev[0].closing_speed_mps >= 1.0


def test_slow_approach_does_not_fire():
    assert _feed(new(), [1.5 - 0.04 * i for i in range(10)]) == []


def test_fast_path_after_three_samples():
    ev = _feed(new(), [2.4, 2.0, 1.6])  # 5 m/s
    assert ev and ev[0].reason == "instant_drop"


def test_wearer_moving_raises_bar():
    m = MotionState()
    t = 0.0
    while t < 10.0:  # walking bounce
        m.push(t, 0, 0, 1.0 + (0.2 if int(t / 0.26) % 2 else -0.2), 5.0, 0, 0)
        t += 0.005
    assert m.moving(10.0)
    assert _feed(new(), [1.70, 1.60, 1.50, 1.40, 1.30, 1.20], motion=m, t0=9.9) == []  # 1.25 m/s
    ev = _feed(new(), [1.78, 1.58, 1.38, 1.18, 0.98], motion=m, t0=9.9)  # 2.5 m/s
    assert ev


def test_gyro_gate_drops_track_and_holds_off():
    m = MotionState()
    m.push(10.0, 0, 0, 1.0, 120.0, 0, 0)
    assert m.gated(10.1) and not m.gated(10.0 + cfg.US_GYRO_HOLDOFF_S + 0.05)
    d = new()
    assert d.update(10.1, 1.0, m) is None and not d.track_ok


def test_stale_or_missing_imu_does_not_gate():
    m = MotionState()
    assert not m.gated(5.0) and not m.moving(5.0)
    m.push(1.0, 0, 0, 1.0, 500.0, 0, 0)
    assert not m.gated(5.0)  # data is 4 s old


def test_accel_only_imu_none_gyro():
    m = MotionState()
    m.push(1.0, 0, 0, 1.0, None, None, None)
    assert m.gyro_dps == 0.0


def test_own_body_range_ignored():
    assert _feed(new(), [0.24, 0.20, 0.16, 0.12, 0.08, 0.06]) == []
    d = new(); d.update(1.0, 0.1)
    assert d.last_distance is None


def test_dropout_gap_breaks_track():
    d = new()
    for i, r in enumerate([1.7, 1.6, 1.5]):
        d.update(10 + i * 0.08, r)
    d.update(10.5, 1.3)
    assert d._hist and len(d._hist) == 1


def test_static_accel_offset_does_not_count_as_moving():
    """The real board reads ~0.94 g at rest; that offset alone used to look like walking."""
    m = MotionState()
    t = 100.0
    for _ in range(600):
        m.push(t, 0.0, 0.0, -0.94, 0.0, 0.0, 0.0)
        t += 0.005
    assert m.dyn_rms_g < 0.01
    assert not m.moving(t)


def test_walking_bounce_still_counts_as_moving():
    m = MotionState()
    t = 100.0
    for i in range(800):
        z = -0.94 - 0.2 * math.sin(2 * math.pi * 2.0 * i * 0.005)
        m.push(t, 0.0, 0.0, z, 0.0, 0.0, 0.0)
        t += 0.005
    assert m.moving(t)
