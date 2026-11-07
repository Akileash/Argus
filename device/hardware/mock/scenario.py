"""Scenario engine: physically consistent synthetic sensor streams (spec §5.6).

A scenario YAML (``device/scenarios/*.yaml``) describes the *world*: how the hat moves
(velocity keyframes, gait, free fall, impacts, pitch), the walls/objects around it, and the
VL53L0X crown distance. One kinematic model then produces every stream, so they agree:

* IMU = specific force (world acceleration + 1 g up) rotated into the hat frame
  (x fwd, y left, z up, pitch about y), so an upright hat at rest reads +1 g on z and a hat in
  free fall reads ~0 g. Gyro y is the derivative of the pitch angle.
* HC-SR04 ranges = ray casts along each sensor's beam axis against walls/floor/ceiling plus
  point objects inside the 15 deg beam, from the *integrated* hat position. So the range slope
  matches the IMU motion (an ego-motion compensating detector can cancel it exactly).
* Noise is seeded and indexed per sample (not per read call), so output does not depend on how
  the consumer chunks its ``read()`` calls.

Sensor azimuths (spec §2.5): R1..R4 at 22.5, 7.5, -7.5, -22.5 deg from straight back; positive
azimuth is the wearer's LEFT (so a board from behind-left hits R1/R2). Beams tilt up 5 deg.
"""

from __future__ import annotations

import math
import os
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict, Field

from argus.contracts import SENSOR_IDS, Clock

G = 9.80665
GRID_HZ = 2000
BEAM_HALF_DEG = 7.5
RANGE_TILT_DEG = 5.0
HC_SR04_MIN_M, HC_SR04_MAX_M = 0.02, 4.0
SENSOR_SLOT_S = 0.03  # one sensor fires every 30 ms; sweep = slot x active sensors
IMU_CLIP_G, GYRO_CLIP_DPS = 16.0, 1000.0
SCENARIO_DIR = Path(os.environ.get("ARGUS_SCENARIOS", Path(__file__).resolve().parents[2] / "scenarios"))


def _beam_axis(sensor: str) -> np.ndarray:
    if sensor == "TOP":
        return np.array([0.0, 0.0, 1.0])
    az = math.radians({"R1": 22.5, "R2": 7.5, "R3": -7.5, "R4": -22.5}[sensor])
    tilt = math.radians(RANGE_TILT_DEG)
    return np.array([-math.cos(tilt) * math.cos(az), math.cos(tilt) * math.sin(az), math.sin(tilt)])


BEAM_AXES = {s: _beam_axis(s) for s in SENSOR_IDS}


# ---------------------------------------------------------------- YAML schema
class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Gait(_M):
    freq_hz: float = 1.9
    start_s: float
    end_s: float
    ramp_s: float = 0.5
    vx_amp_mps: float = 0.0
    vy_amp_mps: float = 0.0
    vz_amp_mps: float = 0.0
    pitch_amp_deg: float = 0.0
    gyro_sway_dps: float = 0.0


class FreeFall(_M):
    t: float
    height_m: float  # duration = sqrt(2h/g)


class Impulse(_M):
    """``hit``: push along ``dir`` then a gentle recoil (net dv = 0). ``stop``: a pulse that brings the
    current velocity to zero (a landing); duration follows from ``peak_g``."""

    t: float
    kind: str = "hit"
    peak_g: float
    dur_ms: float = 10.0
    dir: tuple[float, float, float] = (1.0, 0.0, 0.0)
    recoil: bool = True


class Wearer(_M):
    hat_height_m: float = 1.7
    vx: list[tuple[float, float]] = Field(default_factory=list)  # [t_s, m/s], linear interp
    vy: list[tuple[float, float]] = Field(default_factory=list)
    vz: list[tuple[float, float]] = Field(default_factory=list)
    pitch_deg: list[tuple[float, float]] = Field(default_factory=list)
    gait: Gait | None = None
    free_fall: list[FreeFall] = Field(default_factory=list)
    impulses: list[Impulse] = Field(default_factory=list)


class Plane(_M):
    axis: str  # x | y | z
    at: float  # world coordinate (m)


class MovingObject(_M):
    start: tuple[float, float, float]  # world position relative to the hat's start (m)
    aim: tuple[float, float, float]
    speed_mps: float
    t_start: float


class World(_M):
    planes: list[Plane] = Field(default_factory=list)
    floor: bool = True
    objects: list[MovingObject] = Field(default_factory=list)


class TofSpec(_M):
    # [t_s, mm | null]: hold the last keyframe, ramp linearly into a numeric next one, null = no target.
    keyframes: list[tuple[float, float | None]] = Field(default_factory=lambda: [(0.0, 62.0)])
    noise_mm: float = 1.5


class EnvSpec(_M):
    co2_ppm: float = 650.0
    temp_c: float = 22.4
    rh_pct: float = 41.0


class ExpectedEvent(_M):
    """A detection the scenario should produce. ``t_window_s`` bounds the detection timestamp
    (scenario seconds); ``sensors`` lists acceptable sensor ids (None = not sensor specific)."""

    type: str
    t_window_s: tuple[float, float]
    sensors: list[str] | None = None
    notes: str = ""


class ScenarioSpec(_M):
    name: str
    description: str = ""
    duration_s: float
    seed: int = 1
    wearer: Wearer = Wearer()
    world: World = World()
    tof: TofSpec = TofSpec()
    env: EnvSpec = EnvSpec()
    expected: list[ExpectedEvent] = Field(default_factory=list)
    # Types a detector may additionally emit without it being a false positive (e.g. repeat warnings).
    allowed_extra: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- kinematics
def _kf(t: np.ndarray, keyframes: list[tuple[float, float]]) -> np.ndarray:
    if not keyframes:
        return np.zeros_like(t)
    xs, ys = zip(*keyframes, strict=True)
    return np.interp(t, xs, ys)


def _gait_envelope(t: np.ndarray, g: Gait) -> np.ndarray:
    up = np.clip((t - g.start_s) / g.ramp_s, 0, 1)
    down = np.clip((g.end_s - t) / g.ramp_s, 0, 1)
    w = np.minimum(up, down)
    return w * w * (3 - 2 * w)  # smoothstep


def _half_sine(t: np.ndarray, t0: float, dur: float, amp: float) -> np.ndarray:
    m = (t >= t0) & (t < t0 + dur)
    return np.where(m, amp * np.sin(np.pi * (t - t0) / dur), 0.0)


class _Kinematics:
    """World-frame hat trajectory on a fixed grid: acceleration, velocity, position, pitch."""

    def __init__(self, spec: ScenarioSpec) -> None:
        dt = 1.0 / GRID_HZ
        n = round(spec.duration_s * GRID_HZ) + 1
        t = np.arange(n) * dt
        w = spec.wearer
        v_prof = np.stack([_kf(t, w.vx), _kf(t, w.vy), _kf(t, w.vz)], axis=1)
        theta = _kf(t, w.pitch_deg)
        sway = np.zeros((n, 2))
        if w.gait:
            g = w.gait
            env = _gait_envelope(t, g)
            ph = 2 * np.pi * g.freq_hz * (t - g.start_s)
            v_prof += env[:, None] * np.sin(ph)[:, None] * np.array([g.vx_amp_mps, g.vy_amp_mps, g.vz_amp_mps])
            theta = theta + g.pitch_amp_deg * env * np.sin(ph + np.pi / 3)
            sway = g.gyro_sway_dps * env[:, None] * np.stack([np.cos(ph + 1.0), 0.6 * np.sin(ph)], axis=1)
        acc = np.gradient(v_prof, dt, axis=0)
        for ff in w.free_fall:
            dur = math.sqrt(2 * ff.height_m / G)
            acc[:, 2] -= G * ((t >= ff.t) & (t < ff.t + dur))
        for imp in w.impulses:
            if imp.kind == "hit":
                d = np.asarray(imp.dir, float)
                d /= np.linalg.norm(d)
                dur = imp.dur_ms / 1e3
                acc += _half_sine(t, imp.t, dur, imp.peak_g * G)[:, None] * d
                if imp.recoil:
                    acc -= _half_sine(t, imp.t + dur, 4 * dur, imp.peak_g * G / 4)[:, None] * d
        for imp in sorted((i for i in w.impulses if i.kind == "stop"), key=lambda i: i.t):
            v_pre = (np.cumsum(acc, axis=0) * dt)[round(imp.t * GRID_HZ)]
            speed = float(np.linalg.norm(v_pre))
            if speed > 1e-6:
                dur = math.pi * speed / (2 * imp.peak_g * G)
                acc += _half_sine(t, imp.t, dur, imp.peak_g * G)[:, None] * (-v_pre / speed)
        acc[-1] = 0.0
        vel = np.cumsum(acc, axis=0) * dt
        pos = np.cumsum(vel, axis=0) * dt + np.array([0.0, 0.0, w.hat_height_m])
        self.t, self.acc, self.vel, self.pos = t, acc, vel, pos
        self.theta_deg = theta
        self.theta_rate_dps = np.gradient(theta, dt)
        self.sway_dps = sway

    def interp(self, arr: np.ndarray, ts: np.ndarray | float) -> np.ndarray:
        if arr.ndim == 1:
            return np.interp(ts, self.t, arr)
        return np.stack([np.interp(ts, self.t, arr[:, i]) for i in range(arr.shape[1])], axis=-1)


# ---------------------------------------------------------------- scenario
_BLOCK = 4096


class Scenario:
    def __init__(self, spec: ScenarioSpec) -> None:
        self.spec = spec
        self.name = spec.name
        self.duration_s = spec.duration_s
        self.expected = spec.expected
        self._kin = _Kinematics(spec)
        self._blocks: dict[tuple[int, int, int], np.ndarray] = {}

    # -- noise, indexed per sample so it is independent of read() chunking
    def _normals(self, stream: int, idx: np.ndarray, width: int) -> np.ndarray:
        out = np.empty((len(idx), width))
        blocks = idx // _BLOCK
        for b in np.unique(blocks):
            key = (stream, int(b), width)
            if key not in self._blocks:
                rng = np.random.default_rng([self.spec.seed, stream, int(b)])
                self._blocks[key] = rng.standard_normal((_BLOCK, width))
            m = blocks == b
            out[m] = self._blocks[key][idx[m] % _BLOCK]
        return out

    # -- IMU
    def imu(self, t_s: np.ndarray, idx: np.ndarray) -> np.ndarray:
        """(N, 6) rows ``ax ay az gx gy gz`` in g and deg/s, with noise, bias and sensor clipping."""
        k = self._kin
        live = (t_s <= self.duration_s)[:, None]
        f = k.interp(k.acc, t_s) / G * live + np.array([0.0, 0.0, 1.0])
        th = np.radians(k.interp(k.theta_deg, t_s))
        c, s = np.cos(th), np.sin(th)
        acc = np.stack([f[:, 0] * c - f[:, 2] * s, f[:, 1], f[:, 0] * s + f[:, 2] * c], axis=1)
        sway = k.interp(k.sway_dps, t_s)
        gyro = np.stack([sway[:, 0], k.interp(k.theta_rate_dps, t_s) * live[:, 0], sway[:, 1]], axis=1)
        z = self._normals(1, idx, 6)
        acc = acc + 0.008 * z[:, :3] + np.array([0.002, -0.0015, 0.0025])  # residual bias after boot cal
        gyro = gyro + 0.25 * z[:, 3:] + np.array([0.4, -0.3, 0.2])
        return np.concatenate([np.clip(acc, -IMU_CLIP_G, IMU_CLIP_G), np.clip(gyro, -GYRO_CLIP_DPS, GYRO_CLIP_DPS)], 1)

    # -- HC-SR04
    def _object_pos(self, o: MovingObject, t: float) -> np.ndarray | None:
        a, b = np.asarray(o.start, float), np.asarray(o.aim, float)
        length = float(np.linalg.norm(b - a))
        travelled = o.speed_mps * (t - o.t_start)
        if travelled < 0 or travelled >= length:
            return None
        return a + (b - a) / length * travelled

    def true_range_m(self, t: float, sensor: str) -> float | None:
        """Noise-free distance the sensor would measure (None = nothing in the beam)."""
        k = self._kin
        p = k.interp(k.pos, t)
        th = math.radians(float(k.interp(k.theta_deg, t)))
        ub = BEAM_AXES[sensor]
        c, s = math.cos(th), math.sin(th)
        u = np.array([ub[0] * c + ub[2] * s, ub[1], -ub[0] * s + ub[2] * c])  # body -> world
        planes = [(pl.axis, pl.at) for pl in self.spec.world.planes]
        if self.spec.world.floor:
            planes.append(("z", 0.0))
        best = math.inf
        for axis, at in planes:
            i = "xyz".index(axis)
            if abs(u[i]) > 1e-6:
                d = (at - p[i]) / u[i]
                if d > 0:
                    best = min(best, d)
        for o in self.spec.world.objects:
            q = self._object_pos(o, t)
            if q is None:
                continue
            rel = (q + np.array([0.0, 0.0, self.spec.wearer.hat_height_m])) - p
            dist = float(np.linalg.norm(rel))
            if dist > 1e-6 and math.degrees(math.acos(min(1.0, float(rel @ u) / dist))) <= BEAM_HALF_DEG:
                best = min(best, dist)
        return best if HC_SR04_MIN_M <= best <= HC_SR04_MAX_M else None

    def range_mm(self, t_s: float, sensor: str, n: int) -> float | None:
        """Noisy reading for sample index ``n`` (n = running sensor-slot count). Includes 3% dropouts."""
        d = self.true_range_m(t_s, sensor)
        z = self._normals(2, np.array([n]), 2)[0]
        if d is None or z[1] > 1.88:
            return None
        return round(float((d + z[0] * (0.003 + 0.002 * d)) * 1000.0), 1)

    # -- VL53L0X
    def tof(self, t_s: float, n: int) -> tuple[float | None, str]:
        kfs = self.spec.tof.keyframes
        cur: tuple[float, float | None] = kfs[0]
        nxt = None
        for i, kf in enumerate(kfs):
            if kf[0] <= t_s:
                cur, nxt = kf, kfs[i + 1] if i + 1 < len(kfs) else None
        value = cur[1]
        if value is not None and nxt is not None and nxt[1] is not None and nxt[0] > cur[0]:
            value += (nxt[1] - value) * (t_s - cur[0]) / (nxt[0] - cur[0])
        if value is None:
            return None, "out_of_range"
        z = self._normals(3, np.array([n]), 1)[0, 0]
        return round(float(value + z * self.spec.tof.noise_mm), 1), "ok"

    # -- SCD41
    def env(self, t_s: float, n: int) -> tuple[float, float, float]:
        e = self.spec.env
        z = self._normals(4, np.array([n]), 3)[0]
        return (round(float(e.co2_ppm + 0.8 * t_s + 6 * z[0]), 1), round(float(e.temp_c + 0.01 * z[1]), 2),
                round(float(e.rh_pct + 0.1 * z[2]), 2))


# ---------------------------------------------------------------- loading + shared runs
def list_scenarios() -> list[str]:
    return sorted(p.stem for p in SCENARIO_DIR.glob("*.yaml"))


def load_scenario(name_or_path: str | Path) -> Scenario:
    p = Path(name_or_path)
    if not p.suffix:
        p = SCENARIO_DIR / f"{name_or_path}.yaml"
    if not p.exists():
        raise FileNotFoundError(f"scenario {name_or_path!r} not found ({p}); available: {list_scenarios()}")
    raw: Any = yaml.safe_load(p.read_text())
    return Scenario(ScenarioSpec.model_validate(raw))


@dataclass
class ScenarioRun:
    """A scenario bound to a clock: ``t0_ns`` is scenario time zero, shared by every mock module so
    their streams stay synchronised (and a hot-swapped module continues where its predecessor was)."""

    scenario: Scenario
    t0_ns: int

    def t_s(self, now_ns: int) -> float:
        return (now_ns - self.t0_ns) / 1e9


_RUNS: weakref.WeakKeyDictionary[Clock, dict[str, ScenarioRun]] = weakref.WeakKeyDictionary()


def get_run(scenario: str | None, clock: Clock) -> ScenarioRun:
    name = scenario or "idle"
    runs = _RUNS.setdefault(clock, {})
    if name not in runs:
        runs[name] = ScenarioRun(load_scenario(name), clock.now_ns())
    return runs[name]


def reset_runs(clock: Clock | None = None) -> None:
    """Forget scenario start times (next module start re-bases scenario time zero)."""
    if clock is None:
        _RUNS.clear()
    else:
        _RUNS.pop(clock, None)
