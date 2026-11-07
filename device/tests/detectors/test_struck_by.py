"""StruckByDetector (spec §4.1) on physically consistent synthetic streams.

Every scenario starts with >= 2 s of stillness so the detector's boot
calibration (spec §4.3) completes. Sim time t maps to clock t + 1 s (synth).
Detector config is always the spec defaults ({}), never tuned per test.
"""

from __future__ import annotations

import math
import time

import numpy as np
import pytest
from synth import (
    Impact,
    Plane,
    Scene,
    SimConfig,
    Sphere,
    Trajectory,
    VelocityProfile,
    of_type,
    run,
    simulate,
    still,
)

from argus.contracts import ImuSample, RangeSample
from argus.detectors.base import DetectorState
from argus.detectors.geometry import adjacency, beam_vectors
from argus.detectors.struck_by import StruckByDetector

HEAD = np.array([0.0, 0.0, 1.7])
CLOCK_OFF = 1.0  # synth maps sim t=0 to clock 1 s
STRUCK = ("struck_by_warning", "struck_by_near_miss", "struck_by_contact")


def sim_t(d) -> float:
    return d.t_ns * 1e-9 - CLOCK_OFF


# ---------------------------------------------------------------- scenario builders
def approach_from(
    yaw_from_back_deg: float, speed=2.5, t_on=4.0, d0=4.5, d_stop=0.3, radius=0.1, elevation_deg=0.0
):
    """Object closing straight on the head along a fixed bearing (yaw + toward left)."""
    y, e = math.radians(yaw_from_back_deg), math.radians(elevation_deg)
    dirv = np.array([-math.cos(y) * math.cos(e), math.sin(y) * math.cos(e), math.sin(e)])

    def centre(t):
        return HEAD + dirv * max(d_stop, d0 - speed * (t - t_on))

    return centre, Sphere(centre, radius, t_on, math.inf)


def true_range(centre, t, radius=0.1):
    return float(np.linalg.norm(centre(t) - HEAD)) - radius


def walking_backward(t_start=3.0, v=1.2, ramp=0.8, cruise=2.12, bob_m=0.015, step_hz=1.8):
    """Backward walk along world -X with gait bob (vertical, ~3 cm p-p at step rate,
    ~1.9 m/s^2 peak), fore-aft speed ripple (+-0.1 m/s) and a +-2 deg nod."""
    t_end = t_start + 2 * ramp + cruise
    prof = VelocityProfile(
        [(t_start, 0.0), (t_start + ramp, -v), (t_start + ramp + cruise, -v), (t_end, 0.0)]
    )

    def window(t):  # smooth on/off of gait oscillations
        if t <= t_start or t >= t_end:
            return 0.0
        return math.sin(math.pi * (t - t_start) / (t_end - t_start)) ** 2

    def pos(t):
        w = window(t)
        ph = 2 * math.pi * step_hz * (t - t_start)
        return np.array(
            [
                prof.pos(t) + w * 0.009 * math.sin(ph),
                0.0,
                HEAD[2] + w * bob_m * math.sin(ph),
            ]
        )

    def rpy(t):
        return (0.0, math.radians(2.0) * window(t) * math.sin(2 * math.pi * step_hz * (t - t_start)), 0.0)

    return Trajectory(pos, rpy), prof.pos(t_end + 10)


# ---------------------------------------------------------------- geometry
def test_beam_geometry_sign_convention():
    u = beam_vectors()
    assert u["R1"][0] < 0 and u["R1"][1] > 0  # back-left
    assert u["R4"][0] < 0 and u["R4"][1] < 0  # back-right
    assert u["TOP"] == pytest.approx((0.0, 0.0, 1.0), abs=1e-12)
    ang = math.degrees(math.acos(-u["R2"][0]))
    assert ang == pytest.approx(7.5)
    for v in u.values():
        assert math.isclose(sum(c * c for c in v), 1.0)
    adj = adjacency()
    assert adj["R1"] == ("R2",) and set(adj["R2"]) == {"R1", "R3"} and adj["TOP"] == ()
    # config override
    u2 = beam_vectors({"sensor_yaw_deg": {"R1": 30.0}})
    assert math.degrees(math.atan2(u2["R1"][1], -u2["R1"][0])) == pytest.approx(30.0)


# ---------------------------------------------------------------- true positive
def test_object_from_behind_left_still_wearer_near_miss():
    centre, sphere = approach_from(20.0, speed=2.5)
    samples = simulate(still(HEAD), Scene(spheres=[sphere]), 8.0, SimConfig(seed=1))
    det = StruckByDetector({})
    dets = run(det, samples)
    nm = of_type(dets, "struck_by_near_miss")
    assert len(nm) == 1
    nm = nm[0]
    assert nm.sensor_id in ("R1", "R2")
    t = sim_t(nm)
    r_true = true_range(centre, t)
    assert nm.range_m == pytest.approx(r_true, abs=0.03)
    assert nm.closing_rate_mps == pytest.approx(2.5, rel=0.1)
    assert nm.ttc_s == pytest.approx(r_true / 2.5, abs=0.1)
    assert nm.range_m < 2.0 and nm.ttc_s < 1.3
    # live warnings follow the near miss, all on the rear array
    warns = of_type(dets, "struck_by_warning")
    assert len(warns) >= 2 and all(w.sensor_id in ("R1", "R2") for w in warns)
    assert warns[0].t_ns == nm.t_ns
    for key in ("v_meas_mps", "v_ego_mps", "v_obj_mps", "ttc_s", "omega_dps", "ranges_m", "consecutive"):
        assert key in nm.debug
    assert abs(nm.debug["v_ego_mps"]) < 0.2  # wearer still
    assert not of_type(dets, "struck_by_contact")


def test_slow_object_does_not_trigger():
    _, sphere = approach_from(-7.5, speed=1.0)
    dets = run(StruckByDetector({}), simulate(still(HEAD), Scene(spheres=[sphere]), 9.0, SimConfig(seed=2)))
    assert not [d for d in dets if d.type in STRUCK]


def test_top_sensor_falling_object():
    """Object dropping onto the head from above -> TOP / top motor."""
    centre, sphere = approach_from(0.0, speed=3.0, d0=3.5, elevation_deg=90.0)
    det = StruckByDetector({})
    dets = run(det, simulate(still(HEAD), Scene(spheres=[sphere]), 7.0, SimConfig(seed=3)))
    nm = of_type(dets, "struck_by_near_miss")
    assert len(nm) == 1 and nm[0].sensor_id == "TOP"
    assert nm[0].ttc_s == pytest.approx(true_range(centre, sim_t(nm[0])) / 3.0, abs=0.1)
    assert det.haptic_request(of_type(dets, "struck_by_warning")[-1].t_ns)[0] == "top"


# ---------------------------------------------------------------- ego-motion false positives
def test_walking_backward_into_wall_no_events():
    traj, x_final = walking_backward()
    wall_x = x_final - 0.5  # stops 0.5 m from the wall
    scene = Scene(planes=[Plane((wall_x, 0, 0), (1, 0, 0))])
    samples = simulate(traj, scene, 9.0, SimConfig(seed=4))
    # sanity: the rear array really does see the wall closing at walking speed
    r2 = [
        (s.t_ns * 1e-9 - CLOCK_OFF, s.range_mm / 1000)
        for s in samples
        if isinstance(s, RangeSample) and s.sensor_id == "R2" and s.range_mm is not None
    ]
    assert min(r for _, r in r2) < 0.6 and max(r for _, r in r2) > 2.4
    # sanity: IMU shows gravity and the accel/decel profile
    imu = [s for s in samples if isinstance(s, ImuSample)]
    assert np.mean([s.az for s in imu[:200]]) == pytest.approx(1.0, abs=0.03)
    det = StruckByDetector({})
    dets = run(det, samples)
    assert not [d for d in dets if d.type in STRUCK]


def fast_backstep(det_cfg):
    """Quick 2 m/s backward step toward a wall 1.9 m behind: raw closing > 1.5 m/s."""
    prof = VelocityProfile([(3.0, 0.0), (3.25, -2.0), (3.6, -2.0), (3.9, 0.0)])
    traj = Trajectory(lambda t: np.array([prof.pos(t), 0.0, HEAD[2]]))
    scene = Scene(planes=[Plane((-1.9, 0, 0), (1, 0, 0))])
    det = StruckByDetector(det_cfg)
    return det, run(det, simulate(traj, scene, 6.0, SimConfig(seed=5)))


def test_fast_backward_step_is_compensated():
    det, dets = fast_backstep({})
    assert not [d for d in dets if d.type in STRUCK]
    # the case is genuinely hard: raw closing rate alone would have fired
    assert any(c["v_meas_mps"] > 1.5 for c in det.candidate_log)
    _, raw = fast_backstep({"ego_compensation": False})
    assert of_type(raw, "struck_by_near_miss"), "scenario must trigger without ego compensation"


def standing_up(det_cfg, seed=6):
    """Quick stand-up from a deep squat under a beam, toward TOP.

    Head rises 0.86 m in 0.9 s (peak 1.9 m/s) while the head pitches from 15 deg
    down to level. The beam is 1.35 m above the crouched head (0.5 m at the end).
    """
    z0 = 0.9
    prof = VelocityProfile([(3.0, 0.0), (3.45, 1.9), (3.9, 0.0)], x0=z0)
    pitch = lambda t: math.radians(15.0) * (1 - min(max((t - 3.0) / 0.9, 0.0), 1.0))
    traj = Trajectory(lambda t: np.array([0.0, 0.0, prof.pos(t)]), lambda t: (0.0, pitch(t), 0.0))
    scene = Scene(planes=[Plane((0, 0, z0 + 1.35), (0, 0, 1))])
    det = StruckByDetector(det_cfg)
    return det, run(det, simulate(traj, scene, 6.0, SimConfig(seed=seed)))


def test_standing_up_under_beam_no_events():
    det, dets = standing_up({})
    assert not [d for d in dets if d.type in STRUCK]
    tops = [c for c in det.candidate_log if c["sensor_id"] == "TOP"]
    assert tops and max(c["v_meas_mps"] for c in tops) > 1.5  # TOP really saw fast closing
    for c in tops:  # ...and the IMU explained most of it
        assert c["v_ego_mps"] > 0.6 * c["v_meas_mps"] and c["v_obj_mps"] < 1.0
    # Without compensation the trigger condition does hold (here for 1 sweep, so
    # this realistic stand-up is only one sweep short of a false alarm).
    det_raw, _ = standing_up({"ego_compensation": False})
    assert any(c["condition"] for c in det_raw.candidate_log if c["sensor_id"] == "TOP")


def test_head_rotation_across_wall_no_events():
    """Wearer 1.0 m in front of a wall (behind), turns head 90 deg and back (peak 180 deg/s)."""

    def yaw(t):
        up = (t - 3.5) / 1.0
        down = (t - 5.0) / 1.0
        f = lambda x: min(max(x, 0.0), 1.0) - math.sin(2 * math.pi * min(max(x, 0.0), 1.0)) / (2 * math.pi)
        return math.radians(90.0) * (f(up) - f(down))

    traj = Trajectory(lambda t: HEAD, lambda t: (0.0, 0.0, yaw(t)))
    scene = Scene(planes=[Plane((-1.0, 0, 0), (1, 0, 0), max_incidence_deg=60.0)])  # rough wall
    det = StruckByDetector({})
    dets = run(det, simulate(traj, scene, 7.5, SimConfig(seed=7)))
    assert not [d for d in dets if d.type in STRUCK]
    assert det.rotation_resets >= 2


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known limitation of the spec's tau=1 s leaky integrator: during a sustained backward "
        "walk faster than v_obj_min (1.5 m/s) the leak forgets the steady velocity, so the "
        "wall's closing rate is no longer explained by ego-motion. Passes with ego_tau_s=2."
    ),
)
def test_brisk_backward_walk_1p6_mps_known_limitation():
    traj, x_final = walking_backward(v=1.6, cruise=1.5)
    scene = Scene(planes=[Plane((x_final - 0.5, 0, 0), (1, 0, 0))])
    dets = run(StruckByDetector({}), simulate(traj, scene, 9.0, SimConfig(seed=4)))
    assert not [d for d in dets if d.type in STRUCK]


def test_brisk_backward_walk_with_longer_tau():
    traj, x_final = walking_backward(v=1.6, cruise=1.5)
    scene = Scene(planes=[Plane((x_final - 0.5, 0, 0), (1, 0, 0))])
    dets = run(StruckByDetector({"ego_tau_s": 2.0}), simulate(traj, scene, 9.0, SimConfig(seed=4)))
    assert not [d for d in dets if d.type in STRUCK]


def test_threat_from_behind_while_walking_forward_still_detected():
    """Wearer walks forward (away) at 1.2 m/s; object from behind at 3.6 m/s world.

    Relative closing is 2.4 m/s; ego-motion must not explain it away.
    """
    prof = VelocityProfile([(2.5, 0.0), (3.3, 1.2)])
    traj = Trajectory(lambda t: np.array([prof.pos(t), 0.0, HEAD[2]]))
    t_on, v_o = 4.5, 3.6

    def centre(t):
        head = traj.pos(t)
        x_obj = prof.pos(t_on) - 4.0 + v_o * (t - t_on)
        return np.array([min(x_obj, head[0] - 0.3), 0.15, HEAD[2]])

    dets = run(
        StruckByDetector({}),
        simulate(traj, Scene(spheres=[Sphere(centre, 0.1, t_on, math.inf)]), 7.0, SimConfig(seed=10)),
    )
    nm = of_type(dets, "struck_by_near_miss")
    assert len(nm) == 1 and nm[0].sensor_id in ("R2", "R3")
    assert nm[0].closing_rate_mps == pytest.approx(2.4, abs=0.4)


# ---------------------------------------------------------------- tracking robustness
def test_beam_handoff_keeps_one_track():
    """Object closes while drifting sideways from R2's beam into R1's beam."""
    t_on, d0, v_r = 4.0, 3.2, 2.2

    def centre(t):
        d = max(0.3, d0 - v_r * (t - t_on))
        bearing = math.radians(7.5 + 15.0 * min(max((t - (t_on + 0.6)) / 0.5, 0.0), 1.0))
        return HEAD + d * np.array([-math.cos(bearing), math.sin(bearing), 0.0])

    sphere = Sphere(centre, 0.05, t_on, math.inf)  # thin pipe: narrow footprint
    det = StruckByDetector({})
    dets = run(det, simulate(still(HEAD), Scene(spheres=[sphere]), 7.0, SimConfig(seed=8)))
    warns = of_type(dets, "struck_by_warning")
    assert len(of_type(dets, "struck_by_near_miss")) == 1
    assert warns
    assert len({w.debug["track_id"] for w in warns}) == 1
    sensors = warns[-1].debug["track_sensors"]
    assert sensors[0] == "R2" and "R1" in sensors and warns[-1].debug["handoffs"] >= 1


def test_dropouts_tolerated():
    centre, sphere = approach_from(20.0, speed=2.5)
    hits = 0
    for seed in range(10):
        dets = run(
            StruckByDetector({}),
            simulate(still(HEAD), Scene(spheres=[sphere]), 8.0, SimConfig(seed=100 + seed, dropout_p=0.15)),
        )
        nm = of_type(dets, "struck_by_near_miss")
        assert len(nm) <= 1
        if nm:
            hits += 1
            assert nm[0].ttc_s == pytest.approx(true_range(centre, sim_t(nm[0])) / 2.5, abs=0.15)
    assert hits >= 8


def test_none_and_out_of_bounds_ranges_do_not_crash_or_trigger():
    det = StruckByDetector({})
    st = DetectorState()
    for k in range(500):  # 2.5 s still IMU -> calibrated
        det.on_imu(ImuSample(k * 5_000_000, 0, 0, 1.0, 0, 0, 0), st)
    assert det.imu.calibrated
    t = 2_500_000_000
    out = []
    for k, r in enumerate([None, 10.0, 5000.0, None, 1200.0, None, 1199.0, 1201.0, None]):
        out += det.on_range(RangeSample(t + k * 150_000_000, "R3", r), st)
    assert out == []


# ---------------------------------------------------------------- state / events
def test_hat_off_no_triggers():
    _, sphere = approach_from(20.0, speed=2.5)
    det = StruckByDetector({})
    dets = run(
        det, simulate(still(HEAD), Scene(spheres=[sphere]), 8.0, SimConfig(seed=1)), hat_on=lambda t: False
    )
    assert dets == []
    assert det.haptic_request(int(6.5e9)) is None


def test_contact_upgrade_on_impact_after_near_miss():
    _, sphere = approach_from(20.0, speed=2.5, d_stop=0.1)  # object reaches the shell
    # contact at the moment the sphere surface reaches the hat (~sim 5.76 s)
    t_hit = 4.0 + (4.5 - 0.1 - 0.1) / 2.5
    cfg = SimConfig(
        seed=1, imu_hz=400, impacts=[Impact(t_hit, peak_g=12.0, dur_s=0.006, direction_hat=(1, -0.3, 0))]
    )
    dets = run(StruckByDetector({}), simulate(still(HEAD), Scene(spheres=[sphere]), 8.0, cfg))
    nm = of_type(dets, "struck_by_near_miss")
    ct = of_type(dets, "struck_by_contact")
    assert len(nm) == 1 and len(ct) == 1
    assert ct[0].t_ns > nm[0].t_ns and (ct[0].t_ns - nm[0].t_ns) * 1e-9 <= 1.5
    assert ct[0].peak_g == pytest.approx(12.0, abs=1.5)
    assert ct[0].sensor_id == nm[0].sensor_id and not ct[0].peak_g_saturated


def test_impact_too_late_is_not_contact():
    _, sphere = approach_from(20.0, speed=2.5)
    cfg = SimConfig(seed=1, imu_hz=400, impacts=[Impact(7.6, peak_g=12.0)])  # ~2.2 s after near miss
    dets = run(StruckByDetector({}), simulate(still(HEAD), Scene(spheres=[sphere]), 8.0, cfg))
    assert len(of_type(dets, "struck_by_near_miss")) == 1
    assert not of_type(dets, "struck_by_contact")


def test_saturated_impact_flagged():
    _, sphere = approach_from(20.0, speed=2.5, d_stop=0.1)
    cfg = SimConfig(seed=1, imu_hz=400, impacts=[Impact(5.75, peak_g=40.0, dur_s=0.006)])
    ct = of_type(
        run(StruckByDetector({}), simulate(still(HEAD), Scene(spheres=[sphere]), 8.0, cfg)),
        "struck_by_contact",
    )
    assert len(ct) == 1 and ct[0].peak_g_saturated


def test_two_separate_approaches_two_near_misses():
    _, s1 = approach_from(20.0, speed=2.5, t_on=4.0)
    c2, _ = approach_from(-20.0, speed=2.5, t_on=8.0)
    s1.t_off = 6.5
    s2 = Sphere(c2, 0.1, 8.0, math.inf)
    dets = run(StruckByDetector({}), simulate(still(HEAD), Scene(spheres=[s1, s2]), 12.0, SimConfig(seed=9)))
    nm = of_type(dets, "struck_by_near_miss")
    assert [d.sensor_id in ("R1", "R2") for d in nm[:1]] == [True]
    assert len(nm) == 2 and nm[1].sensor_id in ("R3", "R4")


# ---------------------------------------------------------------- haptics
def test_haptic_mapping():
    det = StruckByDetector({})
    assert det.pulse_rate(1.3) == pytest.approx(4.0)
    assert det.pulse_rate(2.0) == pytest.approx(4.0)
    assert det.pulse_rate(0.49) == "continuous"
    mid = det.pulse_rate(0.9)
    assert 4.0 < mid < 10.0
    assert det.haptic_request(0) is None
    _, sphere = approach_from(-20.0, speed=2.5)
    reqs = []
    samples = simulate(still(HEAD), Scene(spheres=[sphere]), 8.0, SimConfig(seed=1))
    dets = run(det, samples, on_step=lambda s: reqs.append((s.t_ns, det.haptic_request(s.t_ns))))
    w = of_type(dets, "struck_by_warning")
    assert w and all(x.sensor_id in ("R3", "R4") for x in w)
    live = [r for t, r in reqs if w[0].t_ns < t <= w[-1].t_ns]
    assert live and all(r is not None and r[0] == "back" for r in live)
    # TTC shrinks during the approach, so the pulse ends continuous
    assert live[-1][1] == "continuous"
    assert all(r is None for t, r in reqs if t < w[0].t_ns)
    assert all(r is None for t, r in reqs if t > w[-1].t_ns + int(0.35e9))


# ---------------------------------------------------------------- performance
def test_per_sample_cost_under_1ms():
    _, sphere = approach_from(20.0, speed=2.5)
    samples = simulate(still(HEAD), Scene(spheres=[sphere]), 8.0, SimConfig(seed=1, imu_hz=400))
    det = StruckByDetector({})
    t0 = time.perf_counter()
    run(det, samples)
    per = (time.perf_counter() - t0) / len(samples)
    assert per < 2e-4  # 0.2 ms; typically ~5 us on a laptop
