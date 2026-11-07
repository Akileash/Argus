"""Synthetic IMU / ToF streams for fall-impact and hat-state tests."""

from __future__ import annotations

import numpy as np

from argus.contracts import ImuSample, TofSample

NS = 1_000_000_000


def _clip(v: float, lim: float = 16.0) -> float:
    return float(max(-lim, min(lim, v)))


def imu(t_ns: int, az: float, ax: float = 0.0, ay: float = 0.0) -> ImuSample:
    return ImuSample(t_ns, _clip(ax), _clip(ay), _clip(az), 0.0, 0.0, 0.0)


def rest(t0_s: float, dur_s: float, hz: int = 1000) -> list[ImuSample]:
    return [imu(int((t0_s + i / hz) * NS), 1.0) for i in range(int(dur_s * hz))]


def freefall(t0_s: float, dur_s: float, hz: int = 1000) -> list[ImuSample]:
    return [imu(int((t0_s + i / hz) * NS), 0.05) for i in range(int(dur_s * hz))]


def walking(t0_s: float, dur_s: float, hz: int = 1000, seed: int = 1) -> list[ImuSample]:
    """Gait bobbing, peaks ~1.8 g, plus a nod."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(int(dur_s * hz)):
        t = i / hz
        a = 1.0 + 0.6 * np.sin(2 * np.pi * 2 * t) + 0.2 * np.sin(2 * np.pi * 5 * t) + rng.normal(0, 0.03)
        out.append(imu(int((t0_s + t) * NS), a, ax=0.3 * np.sin(2 * np.pi * 1 * t)))
    return out


def spike(t0_s: float, g: float, ms: float = 3.0, hz: int = 1000, tail_s: float = 0.2) -> list[ImuSample]:
    """Half-sine impact pulse of `ms` duration starting at t0_s, then rest for tail_s."""
    out = []
    n = max(1, int(ms * hz / 1000))
    for i in range(n):
        a = 1.0 + (g - 1.0) * np.sin(np.pi * (i + 0.5) / n)
        out.append(imu(int((t0_s + i / hz) * NS), a))
    out += rest(t0_s + n / hz, tail_s, hz)
    return out


def drop(t0_s: float, free_fall_s: float, landing_g: float = 12.0, landing_ms: float = 6.0,
         hz: int = 1000) -> list[ImuSample]:
    """Free fall (~0.05 g) then a half-sine landing pulse, then settle 0.3 s."""
    out = freefall(t0_s, free_fall_s, hz)
    return out + spike(t0_s + free_fall_s, landing_g, landing_ms, hz, tail_s=0.3)


def tof_series(t0_s: float, dur_s: float, mm: float | None, hz: int = 30, status: str | None = None,
               jitter: float = 1.0, seed: int = 3) -> list[TofSample]:
    rng = np.random.default_rng(seed)
    out = []
    for i in range(int(dur_s * hz)):
        t = int((t0_s + i / hz) * NS)
        if mm is None:
            out.append(TofSample(t, None, status or "out_of_range"))
        else:
            out.append(TofSample(t, float(mm + rng.normal(0, jitter)), status or "ok"))
    return out


def merge(imu_s: list[ImuSample], tof_s: list[TofSample]):
    """Time-ordered interleave; returns [('imu'|'tof', sample)]."""
    ev = [(s.t_ns, 0, "imu", s) for s in imu_s] + [(s.t_ns, 1, "tof", s) for s in tof_s]
    ev.sort(key=lambda e: (e[0], e[1]))
    return [(k, s) for _, _, k, s in ev]
