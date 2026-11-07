"""Madgwick orientation, gravity removal and boot calibration (spec §2.6, §4.1, §4.3)."""

from __future__ import annotations

import math

import numpy as np
import pytest
from synth import Scene, SimConfig, Trajectory, simulate, still

from argus.contracts import ImuSample
from argus.detectors.orientation import (
    BootCalibrator,
    OrientationFilter,
    quat_from_gravity,
    quat_rotate,
    quat_rotate_inv,
)

HEAD = np.array([0.0, 0.0, 1.7])


def imu_only(samples):
    return [s for s in samples if isinstance(s, ImuSample)]


def up_error_deg(f: OrientationFilter, R: np.ndarray) -> float:
    """Angle between estimated and true world-up, both expressed in the hat frame."""
    g_true = R.T @ np.array([0.0, 0.0, 1.0])
    g_est = np.array(f.to_hat((0.0, 0.0, 1.0)))
    return math.degrees(math.acos(min(1.0, float(g_true @ g_est))))


def calibrated_filter(samples, cfg=None):
    cal = None
    bc = BootCalibrator(cfg)
    rest = []
    for s in samples:
        if cal is None:
            cal = bc.feed(s)
        else:
            rest.append(s)
    assert cal is not None
    return OrientationFilter(cfg, cal), cal, rest


def test_quaternion_helpers_roundtrip():
    q = quat_from_gravity((0.3, -0.2, 0.9))
    up = quat_rotate(q, (0.3, -0.2, 0.9))
    n = math.sqrt(0.3**2 + 0.2**2 + 0.9**2)
    assert up == pytest.approx((0.0, 0.0, n), abs=1e-9)
    v = (0.1, 0.7, -0.4)
    assert quat_rotate_inv(q, quat_rotate(q, v)) == pytest.approx(v, abs=1e-12)
    assert quat_rotate(quat_from_gravity((0, 0, -1)), (0, 0, -1)) == pytest.approx((0, 0, 1), abs=1e-12)


def test_boot_calibration_bias_and_gravity():
    rpy = (math.radians(8), math.radians(-12), math.radians(30))
    cfg = SimConfig(seed=11, gyro_bias_dps=(2.0, -1.5, 0.8))
    samples = imu_only(simulate(still(HEAD, rpy), Scene(), 3.0, cfg))
    f, cal, _ = calibrated_filter(samples)
    assert cal.restarts == 0
    assert cal.gyro_bias_dps == pytest.approx((2.0, -1.5, 0.8), abs=0.05)
    assert (cal.t_ns - samples[0].t_ns) * 1e-9 == pytest.approx(2.0, abs=0.01)
    # initial attitude from gravity; error only from the (unobservable) accel bias
    assert up_error_deg(f, Trajectory(lambda t: HEAD, lambda t: rpy).R(0)) < 1.5


def test_boot_calibration_restarts_on_motion():
    """Hat bumped 1 s into the still window -> window restarts (spec §4.3)."""
    still_s = imu_only(simulate(still(HEAD), Scene(), 6.0, SimConfig(seed=12)))
    bump_t = still_s[0].t_ns + int(1.0e9)
    samples = []
    for s in still_s:
        if bump_t <= s.t_ns < bump_t + int(0.1e9):  # someone nudges the hat: 25 deg/s, 0.1 g
            s = ImuSample(s.t_ns, s.ax + 0.1, s.ay, s.az, s.gx + 25.0, s.gy, s.gz)
        samples.append(s)
    bc = BootCalibrator()
    cal = None
    for s in samples:
        cal = bc.feed(s) or cal
    assert cal is not None and cal.restarts >= 1
    assert cal.t_ns >= bump_t + int(2.0e9)
    assert cal.gyro_bias_dps == pytest.approx(SimConfig().gyro_bias_dps, abs=0.05)


def test_filter_converges_to_correct_tilt_from_wrong_start():
    rpy = (math.radians(10), math.radians(-20), 0.0)
    R = Trajectory(lambda t: HEAD, lambda t: rpy).R(0)
    samples = imu_only(simulate(still(HEAD, rpy), Scene(), 40.0, SimConfig(seed=13, gyro_bias_dps=(0, 0, 0))))
    f = OrientationFilter({})  # identity start: 22 deg wrong
    errs = []
    for s in samples:
        f.update(s)
        errs.append(up_error_deg(f, R))
    assert errs[10] > 20
    assert max(errs[-200:]) < 1.0  # converged (within accel-bias limit)
    assert errs[int(len(errs) * 0.75)] < 1.5


def test_filter_tracks_nodding_and_yaw_with_gravity_present():
    """Nod ±20 deg at 0.5 Hz, roll ±10 deg at 0.3 Hz, yaw spin 90 deg/s."""

    def rpy(t):
        if t < 2.5:
            return (0.0, 0.0, 0.0)
        tt = t - 2.5
        return (
            math.radians(10) * math.sin(2 * math.pi * 0.3 * tt),
            math.radians(20) * math.sin(2 * math.pi * 0.5 * tt),
            math.radians(90) * tt,
        )

    traj = Trajectory(lambda t: HEAD, rpy)
    samples = imu_only(simulate(traj, Scene(), 20.0, SimConfig(seed=14)))
    f, _, rest = calibrated_filter(samples)
    worst = 0.0
    for s in rest:
        f.update(s)
        worst = max(worst, up_error_deg(f, traj.R(s.t_ns * 1e-9 - 1.0)))
    assert worst < 2.0


def test_gravity_removal_still_and_known_acceleration():
    rpy = (math.radians(5), math.radians(15), math.radians(-40))
    # still: linear accel ~ 0 in both frames
    samples = imu_only(simulate(still(HEAD, rpy), Scene(), 5.0, SimConfig(seed=15)))
    f, _, rest = calibrated_filter(samples)
    lw, lh = [], []
    for s in rest:
        f.update(s)
        lw.append(f.lin_acc_world)
        lh.append(f.lin_acc_hat)
    assert np.linalg.norm(np.mean(lw, axis=0)) < 0.05
    assert np.linalg.norm(np.mean(lh, axis=0)) < 0.05

    # 1 m/s^2 world-+X push from t=3 s on a yawed (90 deg) level hat -> hat -y
    yaw = (0.0, 0.0, math.radians(90))
    traj = Trajectory(lambda t: HEAD + np.array([0.5 * max(t - 3.0, 0.0) ** 2, 0.0, 0.0]), lambda t: yaw)
    samples = imu_only(simulate(traj, Scene(), 4.0, SimConfig(seed=16)))
    f, _, rest = calibrated_filter(samples)
    hats = []
    for s in rest:
        f.update(s)
        # first 0.5 s of the push: a *sustained* push slowly leaks into tilt
        # (accel-only gravity reference, ~2*beta rad/s), which is expected
        if 3.05 < s.t_ns * 1e-9 - 1.0 < 3.55:
            hats.append(f.lin_acc_hat)
    m = np.mean(hats, axis=0)
    # world +X seen from a hat yawed +90 deg (x points to world +Y) is hat -y
    assert m == pytest.approx([0.0, -1.0, 0.0], abs=0.08)


def test_per_sample_cost():
    import time

    samples = imu_only(simulate(still(HEAD), Scene(), 5.0, SimConfig(seed=17)))
    f = OrientationFilter({})
    t0 = time.perf_counter()
    for s in samples:
        f.update(s)
    assert (time.perf_counter() - t0) / len(samples) < 1e-4
