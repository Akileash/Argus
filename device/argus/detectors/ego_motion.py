"""Ego-velocity estimate v_hat for struck-by compensation (spec §4.1 step 3).

Pipeline per IMU sample:
    boot calibration (still window)  ->  Madgwick orientation  ->  gravity removal
    -> world-frame linear accel  ->  leaky integrator (tau ~ 1 s)  ->  ZUPT
    -> v_hat rotated back into the hat frame.

Integration happens in the level (world) frame so head rotation does not smear
velocity between axes; only the final value is expressed in the hat frame.
Heading drift (no magnetometer) cancels because the same quaternion is used to
go world -> hat.

Drift bound: a residual accel bias b (gravity-removal / tilt error, ~0.01-0.02 g
for small tilt errors) produces at most |v| = b * tau of velocity error with the
leak (0.1-0.2 m/s at tau = 1 s), and zero while ZUPT is active.
The leak also high-passes *true* sustained velocity: a constant walking speed
decays out of v_hat with time constant tau. Over the ~0.45 s track window this
is acceptable for the short, sudden motions we must not mistake for threats.

cfg keys (thresholds.struck_by, all optional):
    ego_tau_s (1.0)             leaky-integrator time constant
    zupt_window_s (0.2)         window over which accel variance is evaluated
    zupt_var_thresh (0.05)      (m/s^2)^2, sum of per-axis variances of world linear accel
    zupt_mean_thresh (0.4)      m/s^2, |mean world linear accel| over the window must also be
                                below this (a smooth, sustained push has low variance but is
                                not "standing still")
    zupt_max_speed_mps (0.5)    ZUPT may only zero a velocity smaller than this. ZUPT exists
                                to remove integration drift, which the leak bounds at
                                bias * tau (~0.1-0.2 m/s); a larger v_hat is real motion that
                                has not yet been cancelled by a measured deceleration (e.g. a
                                constant-velocity segment, which an accelerometer cannot see).
                                It still decays through the leak.
    + orientation.py keys (madgwick_beta, accel_gate_g, max_dt_s, calib_*)
"""

from __future__ import annotations

import math
from collections import deque

from argus.contracts import ImuSample

from .orientation import BootCalibrator, Calibration, OrientationFilter

Vec3 = tuple[float, float, float]


class EgoMotionEstimator:
    """Leaky integrator + zero-velocity update on world-frame linear acceleration."""

    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.tau_s = float(cfg.get("ego_tau_s", 1.0))
        self.zupt_window_ns = int(float(cfg.get("zupt_window_s", 0.2)) * 1e9)
        self.zupt_var_thresh = float(cfg.get("zupt_var_thresh", 0.05))
        self.zupt_mean_thresh = float(cfg.get("zupt_mean_thresh", 0.4))
        self.zupt_max_speed = float(cfg.get("zupt_max_speed_mps", 0.5))
        self.max_dt_s = float(cfg.get("max_dt_s", 0.05))
        self.reset()

    def reset(self) -> None:
        self.v_world: list[float] = [0.0, 0.0, 0.0]
        self.last_t_ns: int | None = None
        self.zupt_active = False
        self.accel_var = 0.0
        self.accel_mean = 0.0
        self._win: deque[tuple[int, float, float, float]] = deque()
        self._s = [0.0, 0.0, 0.0]
        self._ss = [0.0, 0.0, 0.0]

    def update(self, t_ns: int, a_world: Vec3) -> Vec3:
        dt = 0.0 if self.last_t_ns is None else (t_ns - self.last_t_ns) * 1e-9
        self.last_t_ns = t_ns
        dt = min(max(dt, 0.0), self.max_dt_s)

        # sliding-window variance (running sums)
        win, s, ss = self._win, self._s, self._ss
        win.append((t_ns, a_world[0], a_world[1], a_world[2]))
        for k in range(3):
            s[k] += a_world[k]
            ss[k] += a_world[k] * a_world[k]
        while win and t_ns - win[0][0] > self.zupt_window_ns:
            _, x, y, z = win.popleft()
            s[0] -= x
            s[1] -= y
            s[2] -= z
            ss[0] -= x * x
            ss[1] -= y * y
            ss[2] -= z * z
        n = len(win)
        var = 0.0
        mean2 = 0.0
        for k in range(3):
            m = s[k] / n
            mean2 += m * m
            var += max(0.0, ss[k] / n - m * m)
        self.accel_var = var
        self.accel_mean = math.sqrt(mean2)
        span_ok = n >= 3 and (t_ns - win[0][0]) >= 0.9 * self.zupt_window_ns

        # leaky integration
        decay = math.exp(-dt / self.tau_s) if self.tau_s > 0 else 0.0
        v = self.v_world
        for k in range(3):
            v[k] = v[k] * decay + a_world[k] * dt

        still = span_ok and var < self.zupt_var_thresh and self.accel_mean < self.zupt_mean_thresh
        speed2 = v[0] * v[0] + v[1] * v[1] + v[2] * v[2]
        self.zupt_active = still and speed2 < self.zupt_max_speed * self.zupt_max_speed
        if self.zupt_active:
            v[0] = v[1] = v[2] = 0.0
        return (v[0], v[1], v[2])


class ImuPipeline:
    """Calibration + orientation + ego-velocity, fed one ImuSample at a time.

    Until boot calibration completes, ``calibrated`` is False and v_hat is zero.
    Calibration only happens once (spec §4.3); pass ``calibration`` to skip it.
    """

    def __init__(self, cfg: dict | None = None, calibration: Calibration | None = None):
        cfg = cfg or {}
        self.calibrator = BootCalibrator(cfg)
        self.filter = OrientationFilter(cfg)
        self.ego = EgoMotionEstimator(cfg)
        self.calibration: Calibration | None = None
        self.v_hat: Vec3 = (0.0, 0.0, 0.0)
        if calibration is not None:
            self._apply(calibration)

    @property
    def calibrated(self) -> bool:
        return self.calibration is not None

    def _apply(self, cal: Calibration) -> None:
        self.calibration = cal
        self.filter.apply_calibration(cal)
        self.ego.reset()
        self.ego.last_t_ns = cal.t_ns

    def update(self, s: ImuSample) -> None:
        if self.calibration is None:
            cal = self.calibrator.feed(s)
            if cal is not None:
                self._apply(cal)
            return
        self.filter.update(s)
        vw = self.ego.update(s.t_ns, self.filter.lin_acc_world)
        self.v_hat = self.filter.to_hat(vw)

    @property
    def v_world(self) -> Vec3:
        v = self.ego.v_world
        return (v[0], v[1], v[2])

    @property
    def omega_norm_dps(self) -> float:
        return self.filter.omega_norm_dps

    @property
    def zupt_active(self) -> bool:
        return self.ego.zupt_active
