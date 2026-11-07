"""Ultrasonic beam geometry in the hat frame (spec §3.2 layout D, §4.1).

Hat frame (contracts.py): x = forward, y = left, z = up.

Sign convention for rear sensors
--------------------------------
Each rear sensor R1..R4 is described by a *yaw angle measured from straight
back* (the -x axis), **positive toward the wearer's left (+y)**, and an
optional *pitch* (elevation, positive = tilted up). So

    u = (-cos(yaw) * cos(pitch), +sin(yaw) * cos(pitch), sin(pitch))

Defaults (spec §3.2): R1 = +22.5 deg (back-left, outermost), R2 = +7.5,
R3 = -7.5, R4 = -22.5 deg (back-right). Together they cover straight back
±30 deg with 15 deg beams. TOP points straight up (+z).

Adjacency (for beam hand-off) is by yaw order among the rear sensors only:
R1-R2, R2-R3, R3-R4. TOP has no neighbours.

Config (thresholds.struck_by):
    sensor_yaw_deg:   {R1: 22.5, R2: 7.5, R3: -7.5, R4: -22.5}
    sensor_pitch_deg: {R1: 0, R2: 0, R3: 0, R4: 0, TOP: 90}
"""

from __future__ import annotations

import math

from argus.contracts import SensorId

REAR_IDS: tuple[SensorId, ...] = ("R1", "R2", "R3", "R4")
DEFAULT_YAW_DEG: dict[str, float] = {"R1": 22.5, "R2": 7.5, "R3": -7.5, "R4": -22.5}
DEFAULT_PITCH_DEG: dict[str, float] = {"R1": 0.0, "R2": 0.0, "R3": 0.0, "R4": 0.0, "TOP": 90.0}

Vec3 = tuple[float, float, float]


def rear_unit_vector(yaw_deg: float, pitch_deg: float = 0.0) -> Vec3:
    """Unit vector for a rear sensor; yaw from straight back, + toward left (+y)."""
    yaw = math.radians(yaw_deg)
    pitch = math.radians(pitch_deg)
    cp = math.cos(pitch)
    return (-math.cos(yaw) * cp, math.sin(yaw) * cp, math.sin(pitch))


def beam_vectors(cfg: dict | None = None) -> dict[SensorId, Vec3]:
    """u_i for every sensor in the hat frame, from cfg (spec defaults otherwise)."""
    cfg = cfg or {}
    yaw = {**DEFAULT_YAW_DEG, **(cfg.get("sensor_yaw_deg") or {})}
    pitch = {**DEFAULT_PITCH_DEG, **(cfg.get("sensor_pitch_deg") or {})}
    out: dict[SensorId, Vec3] = {}
    for sid in REAR_IDS:
        out[sid] = rear_unit_vector(float(yaw[sid]), float(pitch[sid]))
    # TOP: elevation from horizontal; 90 deg = straight up. A non-90 value tilts it backward.
    tp = math.radians(float(pitch["TOP"]))
    out["TOP"] = (-math.cos(tp), 0.0, math.sin(tp))
    return out


def adjacency(cfg: dict | None = None) -> dict[SensorId, tuple[SensorId, ...]]:
    """Neighbouring rear beams, ordered by configured yaw. TOP has none."""
    cfg = cfg or {}
    yaw = {**DEFAULT_YAW_DEG, **(cfg.get("sensor_yaw_deg") or {})}
    order = sorted(REAR_IDS, key=lambda s: float(yaw[s]))
    adj: dict[SensorId, tuple[SensorId, ...]] = {"TOP": ()}
    for k, sid in enumerate(order):
        nb = []
        if k > 0:
            nb.append(order[k - 1])
        if k < len(order) - 1:
            nb.append(order[k + 1])
        adj[sid] = tuple(nb)
    return adj


def dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
