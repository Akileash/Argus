"""Physically consistent synthetic IMU + HC-SR04 streams for detector tests.

Owned by the M2.1 tests (independent of the scenario mocks).

Model
-----
- The head is a rigid body with world position p(t) (m, z up) and attitude
  R(t) = Rz(yaw) Ry(pitch) Rx(roll) mapping hat -> world.
- IMU (at the hat origin): accel = R^T (p''(t) + g_up) / g0 in g, gyro = body
  rate from R^T dR/dt in deg/s, both with bias + white noise, clipped to ±16 g.
- HC-SR04 (at the hat origin, beam u_i from argus.detectors.geometry): first
  echo inside a 15 deg cone.
    * plane: nearest cone ray to the normal; echo only if that ray's incidence
      is <= ``max_incidence_deg``.
    * sphere: hit if the centre is within half-angle + asin(R / d) of the beam
      axis; range = d - R.
  So range rate is *derived* from geometry: for a point-like object it is
  (v_obj - v_head) . u, never injected directly.
- Interleaved firing: R1 -> R3 -> TOP -> R2 -> R4, 30 ms slots, 150 ms sweep.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import pairwise

import numpy as np

from argus.contracts import ImuSample, RangeSample
from argus.detectors.base import Detection, DetectorState
from argus.detectors.geometry import beam_vectors

G0 = 9.80665
FIRE_ORDER = ("R1", "R3", "TOP", "R2", "R4")


# ---------------------------------------------------------------- motion primitives
class VelocityProfile:
    """1-D velocity keyframes [(t, v)] joined by cosine ramps; analytic position."""

    def __init__(self, keyframes: list[tuple[float, float]], x0: float = 0.0):
        self.k = sorted(keyframes)
        self.x0 = x0
        # position at each keyframe
        self._xk = [x0]
        for (ta, va), (tb, vb) in pairwise(self.k):
            self._xk.append(self._xk[-1] + 0.5 * (va + vb) * (tb - ta))

    def vel(self, t: float) -> float:
        k = self.k
        if t <= k[0][0]:
            return k[0][1]
        for (ta, va), (tb, vb) in pairwise(k):
            if t <= tb:
                tau = (t - ta) / (tb - ta)
                return va + (vb - va) * (1 - math.cos(math.pi * tau)) / 2
        return k[-1][1]

    def pos(self, t: float) -> float:
        k = self.k
        if t <= k[0][0]:
            return self.x0 + k[0][1] * (t - k[0][0])
        for i, ((ta, va), (tb, vb)) in enumerate(pairwise(k)):
            if t <= tb:
                T = tb - ta
                tau = (t - ta) / T
                return (
                    self._xk[i]
                    + va * T * tau
                    + 0.5 * (vb - va) * T * (tau - math.sin(math.pi * tau) / math.pi)
                )
        return self._xk[-1] + k[-1][1] * (t - k[-1][0])


def smooth_step(t: float, t0: float, T: float) -> float:
    """0 -> 1 over [t0, t0+T] with a sin^2 velocity profile (zero velocity at both ends)."""
    tau = min(max((t - t0) / T, 0.0), 1.0)
    return tau - math.sin(2 * math.pi * tau) / (2 * math.pi)


def rot_rpy(roll: float, pitch: float, yaw: float) -> np.ndarray:
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])
    rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]])
    return rz @ ry @ rx


@dataclass
class Trajectory:
    """Head pose. pos(t) -> world xyz (m); rpy(t) -> (roll, pitch, yaw) rad, hat -> world."""

    pos: Callable[[float], np.ndarray]
    rpy: Callable[[float], tuple[float, float, float]] = lambda t: (0.0, 0.0, 0.0)

    def R(self, t: float) -> np.ndarray:
        return rot_rpy(*self.rpy(t))

    def acc(self, t: float, h: float = 1e-3) -> np.ndarray:
        return (self.pos(t + h) - 2 * self.pos(t) + self.pos(t - h)) / (h * h)

    def vel(self, t: float, h: float = 1e-4) -> np.ndarray:
        return (self.pos(t + h) - self.pos(t - h)) / (2 * h)

    def omega_body(self, t: float, h: float = 1e-4) -> np.ndarray:
        """Body-frame angular rate (rad/s): vee(R^T dR/dt)."""
        R = self.R(t)
        dR = (self.R(t + h) - self.R(t - h)) / (2 * h)
        W = R.T @ dR
        return np.array([W[2, 1] - W[1, 2], W[0, 2] - W[2, 0], W[1, 0] - W[0, 1]]) / 2


def still(p=(0.0, 0.0, 1.7), rpy=(0.0, 0.0, 0.0)) -> Trajectory:
    pv = np.array(p, float)
    return Trajectory(pos=lambda t: pv, rpy=lambda t: rpy)


# ---------------------------------------------------------------- scene
@dataclass
class Plane:
    point: tuple[float, float, float]
    normal: tuple[float, float, float]
    max_incidence_deg: float = 45.0


@dataclass
class Sphere:
    """Moving object, centre(t) in world m; exists only for t in [t_on, t_off]."""

    centre: Callable[[float], np.ndarray]
    radius: float = 0.1
    t_on: float = -math.inf
    t_off: float = math.inf


@dataclass
class Scene:
    planes: list[Plane] = field(default_factory=list)
    spheres: list[Sphere] = field(default_factory=list)


def raycast(
    origin: np.ndarray,
    d: np.ndarray,
    scene: Scene,
    t: float,
    half_angle_deg: float = 7.5,
    max_range: float = 4.0,
) -> float | None:
    best = math.inf
    ha = math.radians(half_angle_deg)
    for pl in scene.planes:
        n = np.array(pl.normal, float)
        n /= np.linalg.norm(n)
        dist = float(np.dot(np.array(pl.point) - origin, n))
        if abs(dist) < 1e-9:
            continue
        nn = n if dist > 0 else -n  # normal pointing away from the sensor, toward the plane
        cos_inc = float(np.dot(d, nn))
        inc = math.acos(max(-1.0, min(1.0, cos_inc)))
        eff = max(0.0, inc - ha)
        if eff > math.radians(pl.max_incidence_deg) or eff >= math.pi / 2:
            continue
        best = min(best, abs(dist) / math.cos(eff))
    for sp in scene.spheres:
        if not (sp.t_on <= t <= sp.t_off):
            continue
        c = sp.centre(t) - origin
        dist = float(np.linalg.norm(c))
        if dist <= sp.radius:
            best = min(best, 0.0)
            continue
        ang = math.acos(max(-1.0, min(1.0, float(np.dot(c, d)) / dist)))
        if ang <= ha + math.asin(sp.radius / dist):
            best = min(best, dist - sp.radius)
    return best if best <= max_range else None


# ---------------------------------------------------------------- stream generation
@dataclass
class Impact:
    t: float
    peak_g: float = 10.0
    dur_s: float = 0.008
    direction_hat: tuple[float, float, float] = (1.0, 0.0, 0.0)


@dataclass
class SimConfig:
    imu_hz: float = 200.0
    sweep_s: float = 0.15
    accel_noise_g: float = 0.008
    gyro_noise_dps: float = 0.1
    accel_bias_g: tuple[float, float, float] = (0.01, -0.008, 0.012)
    gyro_bias_dps: tuple[float, float, float] = (1.2, -0.7, 0.4)
    range_noise_m: float = 0.003
    dropout_p: float = 0.0
    seed: int = 0
    impacts: list[Impact] = field(default_factory=list)
    beam_cfg: dict | None = None


def simulate(
    traj: Trajectory, scene: Scene, duration_s: float, cfg: SimConfig | None = None, t_offset_s: float = 1.0
) -> list[ImuSample | RangeSample]:
    """Generate a merged, time-ordered stream. Sim time 0 maps to t_offset_s on the clock."""
    cfg = cfg or SimConfig()
    rng = np.random.default_rng(cfg.seed)
    u = {k: np.array(v) for k, v in beam_vectors(cfg.beam_cfg).items()}
    out: list[ImuSample | RangeSample] = []
    off_ns = int(t_offset_s * 1e9)
    up = np.array([0.0, 0.0, G0])

    n_imu = math.floor(duration_s * cfg.imu_hz)
    ab = np.array(cfg.accel_bias_g)
    gb = np.array(cfg.gyro_bias_dps)
    for k in range(n_imu):
        t = k / cfg.imu_hz
        R = traj.R(t)
        f = R.T @ (traj.acc(t) + up) / G0
        for imp in cfg.impacts:
            if imp.t <= t <= imp.t + imp.dur_s:
                dvec = np.array(imp.direction_hat, float)
                f = f + dvec / np.linalg.norm(dvec) * imp.peak_g * math.sin(math.pi * (t - imp.t) / imp.dur_s)
        f = np.clip(f + ab + rng.normal(0, cfg.accel_noise_g, 3), -16.0, 16.0)
        w = np.degrees(traj.omega_body(t)) + gb + rng.normal(0, cfg.gyro_noise_dps, 3)
        out.append(
            ImuSample(
                off_ns + round(t * 1e9),
                float(f[0]),
                float(f[1]),
                float(f[2]),
                float(w[0]),
                float(w[1]),
                float(w[2]),
            )
        )

    slot = cfg.sweep_s / len(FIRE_ORDER)
    n_sweeps = math.floor(duration_s / cfg.sweep_s)
    for n in range(n_sweeps):
        for j, sid in enumerate(FIRE_ORDER):
            t = n * cfg.sweep_s + j * slot
            R = traj.R(t)
            r = raycast(traj.pos(t), R @ u[sid], scene, t)
            if r is not None:
                r = max(0.0, r + rng.normal(0, cfg.range_noise_m))
            if rng.random() < cfg.dropout_p:
                r = None
            out.append(RangeSample(off_ns + round(t * 1e9), sid, None if r is None else round(r * 1000.0)))
    out.sort(key=lambda s: s.t_ns)
    return out


def run(
    detector,
    samples,
    hat_on: Callable[[float], bool] = lambda t: True,
    on_step: Callable[[object], None] | None = None,
) -> list[Detection]:
    """Feed samples to a Detector in order; hat_on(t_s) gives DetectorState.hat_on."""
    dets: list[Detection] = []
    state = DetectorState()
    for s in samples:
        state.hat_on = hat_on(s.t_ns * 1e-9)
        if isinstance(s, ImuSample):
            dets.extend(detector.on_imu(s, state))
        else:
            dets.extend(detector.on_range(s, state))
        if on_step is not None:
            on_step(s)
    return dets


def of_type(dets: list[Detection], typ: str) -> list[Detection]:
    return [d for d in dets if d.type == typ]
