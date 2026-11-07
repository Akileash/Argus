"""Ego-velocity: leaky integrator + ZUPT (spec §4.1 step 3)."""

from __future__ import annotations

import math

import numpy as np
import pytest
from synth import Scene, SimConfig, Trajectory, VelocityProfile, simulate

from argus.contracts import ImuSample
from argus.detectors.ego_motion import EgoMotionEstimator, ImuPipeline
from argus.detectors.orientation import G0

HEAD = np.array([0.0, 0.0, 1.7])


def imu_only(samples):
    return [s for s in samples if isinstance(s, ImuSample)]


def standing_sway(t):
    """Physiological sway: ~1 cm at 0.3 Hz fore-aft, 0.6 cm at 0.17 Hz lateral, +-1 deg nod."""
    p = HEAD + np.array(
        [0.01 * math.sin(2 * math.pi * 0.3 * t), 0.006 * math.sin(2 * math.pi * 0.17 * t + 1), 0.0]
    )
    return p


def sway_rpy(t):
    return (0.0, math.radians(1.0) * math.sin(2 * math.pi * 0.25 * t), math.radians(3.0) * math.sin(0.1 * t))


def drift_after_calibration(samples, t_from_ns, bias_g=(0.015, -0.01, 0.0)):
    """Accelerometer bias drifts (e.g. temperature) after the boot calibration."""
    out = []
    for s in samples:
        if s.t_ns >= t_from_ns:
            s = ImuSample(s.t_ns, s.ax + bias_g[0], s.ay + bias_g[1], s.az + bias_g[2], s.gx, s.gy, s.gz)
        out.append(s)
    return out


def run_pipeline(samples, cfg=None):
    p = ImuPipeline(cfg or {})
    speeds, zupt = [], []
    for s in samples:
        p.update(s)
        if p.calibrated:
            speeds.append(float(np.linalg.norm(p.v_hat)))
            zupt.append(p.zupt_active)
    return p, np.array(speeds), np.array(zupt)


def test_uncalibrated_pipeline_reports_zero():
    p = ImuPipeline({})
    p.update(ImuSample(0, 0.5, 0.0, 0.9, 30.0, 0.0, 0.0))
    assert not p.calibrated and p.v_hat == (0.0, 0.0, 0.0)


def test_zupt_bounds_drift_standing_still_30s():
    raw = imu_only(simulate(Trajectory(standing_sway, sway_rpy), Scene(), 32.5, SimConfig(seed=21)))
    samples = drift_after_calibration(raw, raw[0].t_ns + int(2.5e9))
    _, speeds, zupt = run_pipeline(samples)
    assert len(speeds) > 30 * 190
    assert speeds.max() < 0.05
    assert zupt.mean() > 0.9

    # Without ZUPT the leak alone bounds the drift at ~ bias * tau (0.18 m/s here)
    _, speeds_nz, _ = run_pipeline(samples, {"zupt_var_thresh": 0.0})
    bound = math.hypot(0.015, 0.01) * G0 * 1.0
    assert speeds_nz.max() <= bound * 1.1
    assert speeds_nz.max() > 3 * speeds.max()


def test_velocity_of_short_motion_in_hat_frame():
    """Head yawed +90 deg; a quick 0.6 s step along world +X (peak 1.2 m/s) appears on hat -y."""
    prof = VelocityProfile([(3.0, 0.0), (3.3, 1.2), (3.6, 0.0)])
    traj = Trajectory(lambda t: np.array([prof.pos(t), 0.0, HEAD[2]]), lambda t: (0.0, 0.0, math.radians(90)))
    samples = imu_only(simulate(traj, Scene(), 5.0, SimConfig(seed=22)))
    p = ImuPipeline({})
    at_peak = None
    for s in samples:
        p.update(s)
        if at_peak is None and s.t_ns * 1e-9 - 1.0 >= 3.3:
            at_peak = p.v_hat
    vx, vy, vz = at_peak
    # leak loses ~15% of a 0.3 s ramp: exp-weighted average of the ramp
    assert vy == pytest.approx(-1.2, rel=0.25)
    assert abs(vx) < 0.1 and abs(vz) < 0.1
    # after the step the estimate returns to ~0 (decel integrated, then ZUPT)
    assert np.linalg.norm(p.v_hat) < 0.05


def test_leaky_integrator_time_constant():
    est = EgoMotionEstimator({"ego_tau_s": 1.0, "zupt_var_thresh": 0.0})
    t = 0
    dt = 5_000_000
    # 0.1 s of 10 m/s^2 -> ~1 m/s, then coast with zero accel
    for _ in range(20):
        t += dt
        est.update(t, (10.0, 0.0, 0.0))
    v0 = est.v_world[0]
    assert v0 == pytest.approx(1.0, rel=0.1)
    for _ in range(200):  # 1 s
        t += dt
        est.update(t, (0.0, 0.0, 0.0))
    assert est.v_world[0] == pytest.approx(v0 * math.exp(-1.0), rel=0.02)


def test_zupt_does_not_erase_real_constant_velocity():
    """A smooth constant-velocity segment has zero accel variance; ZUPT must not zero 1 m/s."""
    est = EgoMotionEstimator({})
    t, dt = 0, 5_000_000
    for _ in range(40):  # 0.2 s at 5 m/s^2
        t += dt
        est.update(t, (5.0, 0.0, 0.0))
    for _ in range(60):  # 0.3 s coast: low variance, zero mean
        t += dt
        est.update(t, (0.0, 0.0, 0.0))
    assert est.v_world[0] > 0.5 and not est.zupt_active
