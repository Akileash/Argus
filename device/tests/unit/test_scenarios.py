"""Scenario physics: streams must be mutually consistent so an ego-motion detector can use them."""

from __future__ import annotations

import itertools
from typing import get_args

import numpy as np
import pytest

from argus.contracts import SENSOR_IDS, ModuleContext
from argus.detectors.base import DetectionType
from hardware.mock.clock import VirtualClock
from hardware.mock.imu import MockImu
from hardware.mock.scenario import BEAM_AXES, G, list_scenarios, load_scenario

SPEC_SCENARIOS = {
    "board_swing_rear", "walk_backward_wall", "stand_up_under_beam", "hat_drop_1m",
    "hat_removed_table", "hat_knocked_off", "idle", "walk_forward",
}
HZ = 400


def imu(name: str):
    sc = load_scenario(name)
    t = np.arange(0, sc.duration_s, 1 / HZ)
    return sc, t, sc.imu(t, np.arange(len(t)))


def mag(m: np.ndarray) -> np.ndarray:
    return np.linalg.norm(m[:, :3], axis=1)


def true_closing(sc, sensor: str, t: float, dt: float = 0.05) -> float | None:
    a, b = sc.true_range_m(t - dt, sensor), sc.true_range_m(t + dt, sensor)
    return None if a is None or b is None else (a - b) / (2 * dt)


def test_all_spec_scenarios_exist():
    assert SPEC_SCENARIOS <= set(list_scenarios())


@pytest.mark.parametrize("name", sorted(SPEC_SCENARIOS))
def test_expected_events_are_well_formed(name):
    sc = load_scenario(name)
    valid = set(get_args(DetectionType))
    for e in sc.expected:
        assert e.type in valid
        assert 0 <= e.t_window_s[0] < e.t_window_s[1] <= sc.duration_s
        assert all(s in SENSOR_IDS for s in e.sensors or [])
    assert set(sc.spec.allowed_extra) <= valid


def test_expected_events_summary():
    exp = {n: [e.type for e in load_scenario(n).expected] for n in SPEC_SCENARIOS}
    assert exp["board_swing_rear"] == ["struck_by_near_miss"]
    assert exp["walk_backward_wall"] == exp["stand_up_under_beam"] == exp["idle"] == exp["walk_forward"] == []
    assert exp["hat_drop_1m"] == ["fall"]
    assert exp["hat_removed_table"] == ["hat_removed"]
    assert exp["hat_knocked_off"] == ["head_impact", "hat_knocked_off"]
    assert load_scenario("board_swing_rear").expected[0].sensors == ["R2", "R3"]


def test_idle_gravity_on_z_and_small_gyro():
    _, _, m = imu("idle")
    assert abs(np.median(m[:, 2]) - 1.0) < 0.01
    assert abs(np.median(m[:, 0])) < 0.02 and abs(np.median(m[:, 1])) < 0.02
    assert np.all(np.abs(m[:, 3:]) < 3)
    assert 0.003 < m[:, 2].std() < 0.02  # realistic noise, not zero


def test_noise_is_seeded_and_independent_of_chunking():
    def run(step: float) -> list[tuple]:
        clock = VirtualClock()
        mod = MockImu(ModuleContext(clock, {}, "walk_forward"))
        mod.start()
        out = []
        for _ in range(round(1.0 / step)):
            clock.advance(step)
            out += [(s.t_ns - 1_000_000_000, s.ax, s.gz) for s in mod.read()]
        return out

    assert run(0.005) == run(0.1)
    a = load_scenario("idle").imu(np.array([0.0]), np.array([0]))
    assert (a == load_scenario("idle").imu(np.array([0.0]), np.array([0]))).all()


def test_walk_backward_imu_matches_range_slope():
    """IMU-integrated velocity must equal the velocity implied by the wall range (ego consistency)."""
    sc, _, m = imu("walk_backward_wall")
    v_imu = np.cumsum(m[:, 0] * G) / HZ
    k = BEAM_AXES["R2"]
    for tt in (2.0, 2.4, 2.8):
        r = true_closing(sc, "R2", tt)  # range shrinking rate
        v_range = -r * abs(k[0])  # hat x-velocity implied by the wall distance (negative = backward)
        i = int(tt * HZ)
        assert abs(v_imu[i - 20 : i + 20].mean() - v_range) < 0.2, (tt, v_imu[i], v_range)
    assert v_imu[int(2.4 * HZ)] < -0.9  # really walking backward at ~1.2 m/s


def test_walk_backward_fools_a_naive_detector_but_not_an_ego_compensated_one():
    sc = load_scenario("walk_backward_wall")
    worst_naive, worst_comp = 0.0, 0.0
    for sid in ("R1", "R2", "R3", "R4"):
        u = BEAM_AXES[sid]
        for tt in np.arange(1.5, 3.3, 0.05):
            r, vm = sc.true_range_m(float(tt), sid), true_closing(sc, sid, float(tt))
            if r is None or vm is None or r >= 2.0:
                continue
            v_ego = float(sc._kin.interp(sc._kin.vel, float(tt)) @ u)
            worst_naive = max(worst_naive, vm)
            worst_comp = max(worst_comp, vm - v_ego)
    assert worst_naive > 1.5, "scenario must be a real trap: raw closing rate above the 1.5 m/s threshold"
    assert worst_comp < 0.5, "...which ego compensation removes"


def test_stand_up_is_vertical_ego_motion_on_top_sensor():
    sc, _, m = imu("stand_up_under_beam")
    vz = np.cumsum((m[:, 2] - 1.0) * G) / HZ
    assert 1.6 < vz.max() < 2.0
    closing = [true_closing(sc, "TOP", float(x)) for x in np.arange(2.2, 2.7, 0.05)]
    assert max(closing) == pytest.approx(1.8, abs=0.1)
    assert sc.true_range_m(0.0, "TOP") == pytest.approx(1.45, abs=0.01)
    assert sc.true_range_m(5.0, "TOP") == pytest.approx(0.46, abs=0.03)
    peak_az = m[int(2.1 * HZ) : int(2.35 * HZ), 2].mean()
    assert peak_az == pytest.approx(1.0 + 4.5 / G, abs=0.08)  # accelerating upward adds to gravity


def test_board_swing_object_closes_at_2_5_mps_from_straight_behind_with_still_wearer():
    sc, _, m = imu("board_swing_rear")
    assert np.all(np.abs(mag(m) - 1.0) < 0.06) and np.all(np.abs(m[:, 3:]) < 3)  # wearer still
    first = next(x for x in np.arange(1.9, 3.0, 0.01) if sc.true_range_m(float(x), "R2") is not None)
    assert 1.95 < first < 2.1 and sc.true_range_m(float(first), "R2") == pytest.approx(2.5, abs=0.1)
    assert true_closing(sc, "R2", 2.4) == pytest.approx(2.5, abs=0.15)
    assert sc.true_range_m(2.4, "R1") is None and sc.true_range_m(2.4, "R4") is None  # inside the MVP zone only
    assert sc.true_range_m(1.0, "R2") is None
    assert sc.true_range_m(2.4, "TOP") == pytest.approx(1.5, abs=0.01)  # only the ceiling above


def longest_run(mask: np.ndarray) -> int:
    best = cur = 0
    for v in mask:
        cur = cur + 1 if v else 0
        best = max(best, cur)
    return best


def test_hat_drop_free_fall_then_impact_then_rest():
    sc, _, m = imu("hat_drop_1m")
    g = mag(m)
    ff_ms = longest_run(g < 0.35) / HZ * 1000
    assert 400 < ff_ms < 480, ff_ms  # ~450 ms for 1 m
    land = int(3.4516 * HZ)
    assert 10 <= g[land : land + 40].max() <= 16.5
    assert np.all(g[: int(2.9 * HZ)] > 0.9)  # held still before release
    assert abs(np.median(g[-400:]) - 1.0) < 0.02  # at rest after landing
    assert np.median(m[-400:, 2]) == pytest.approx(np.cos(np.radians(50)), abs=0.03)  # tilted on the floor
    assert abs(sc._kin.vel[-1]).max() < 1e-3  # kinematics integrate back to rest
    assert abs(m[int(3.2 * HZ), 4]) > 80  # tumbling during the fall (gyro y)
    assert [sc.tof(x, 0)[1] for x in (0.0, 5.0, 7.0)] == ["out_of_range"] * 3  # never on a head


def test_hat_drop_height_matches_free_fall_time():
    _, _, m = imu("hat_drop_1m")
    ff = longest_run(mag(m) < 0.35) / HZ
    assert 0.5 * G * ff**2 == pytest.approx(1.0, abs=0.2)  # h = 1/2 g t^2 (spec §4.2)


def test_hat_removed_is_calm_and_tof_leaves_baseline():
    sc, _, m = imu("hat_removed_table")
    g = mag(m)
    assert g.max() < 1.5 and g.min() > 0.6  # never an impact (>3 g) or free fall (<0.35 g)
    assert sc.tof(2.0, 0)[0] == pytest.approx(62, abs=8)
    assert sc.tof(5.4, 0)[0] > 92  # past baseline + 30 mm
    assert sc.tof(6.0, 0) == (None, "out_of_range") and sc.tof(13.0, 0)[1] == "out_of_range"


def test_hat_knocked_off_has_impact_then_tof_leaves_without_free_fall():
    sc, _, m = imu("hat_knocked_off")
    g = mag(m)
    peak = g[int(4.9 * HZ) : int(5.2 * HZ)].max()
    assert 6.0 <= peak <= 9.5
    assert g.min() > 0.35  # not a fall
    later = g[int(5.5 * HZ) : int(6.0 * HZ)].max()
    assert 3.0 < later < 6.0  # soft landing: >3 g but below the 6 g impact threshold
    tof_off = next(x for x in np.arange(4.9, 6, 0.01) if sc.tof(float(x), 0)[0] is None)
    assert 5.0 < tof_off < 5.3  # ToF leaves just after the blow (within the 2 s knocked-off lookback)
    assert np.all(np.abs(m[:, :3]) <= 16.0)


def test_range_dropouts_and_bounds():
    sc = load_scenario("idle")
    vals = [sc.range_mm(0.15 * n / 5, SENSOR_IDS[n % 5], n) for n in range(2000)]
    rear = [v for i, v in enumerate(vals) if i % 5 != 4]
    drop = sum(v is None for v in rear) / len(rear)
    assert 0.01 < drop < 0.07  # ~3% HC-SR04 dropouts
    assert all(20 <= v <= 4000 for v in vals if v is not None)
    assert np.mean([v for i, v in enumerate(vals) if i % 5 == 1 and v]) == pytest.approx(2230, abs=15)


def test_walk_forward_ranges_only_grow():
    sc = load_scenario("walk_forward")
    rs = [sc.true_range_m(float(x), "R2") for x in np.arange(2.0, 4.0, 0.25)]
    rs = [r for r in rs if r is not None]
    assert len(rs) >= 4
    assert all(b > a for a, b in itertools.pairwise(rs))
    assert all(true_closing(sc, "R2", float(x)) < 0 for x in np.arange(2.5, 3.5, 0.25))


def test_mock_range_fires_only_the_mvp_pair_by_default_and_honours_order():
    from hardware.mock.clock import VirtualClock
    from hardware.mock.range import create

    def seen(options):
        clock = VirtualClock()
        r = create(ModuleContext(clock, options, "idle"))
        r.start()
        clock.advance(1.0)
        return {s.sensor_id for s in r.read()}

    assert seen({}) == {"R2", "R3"}
    assert seen({"order": ["R1", "R2", "R3", "R4", "TOP"]}) == set(SENSOR_IDS)
