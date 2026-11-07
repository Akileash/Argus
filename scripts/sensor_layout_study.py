#!/usr/bin/env python3
"""Compare ultrasonic layouts by running the REAL struck-by detector on simulated approaches.

Run from device/:  uv run python ../scripts/sensor_layout_study.py [--out results.json]

What this can and can't tell you:
- It measures the trade-off between sweep time (fewer sensors = faster) and coverage (gaps).
- The simulation assumes every object in a beam returns an echo, so it is optimistic about thin or
  soft objects. The "pipe, seen only inside 1.5 m" case partly corrects for that.
- It is NOT a substitute for the bench beam test (spec §3.2). Real beam width decides the rear count.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics as st
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "device"))

from argus.contracts import RangeSample  # noqa: E402
from argus.detectors.geometry import beam_vectors  # noqa: E402
from argus.detectors.struck_by import StruckByDetector  # noqa: E402
from tests.detectors import synth  # noqa: E402
from tests.detectors.test_struck_by import walking_backward  # noqa: E402

HEAD = np.array([0.0, 0.0, 1.7])
SLOT_S = 0.03
HEAD_RADIUS_M = 0.15  # "contact" = object surface reaches the hat
USEFUL_LEAD_S = 0.25  # feel the buzz and start moving; a rough planning number, not a measurement
FAR = 170.0  # yaw for unused rear ids, so they are never anyone's neighbour

LAYOUTS = {
    "A 4 rear + top": ({"R1": 22.5, "R2": 7.5, "R3": -7.5, "R4": -22.5}, ("R1", "R3", "TOP", "R2", "R4")),
    "B 4 rear": ({"R1": 22.5, "R2": 7.5, "R3": -7.5, "R4": -22.5}, ("R1", "R3", "R2", "R4")),
    "C 3 rear wide + top": ({"R1": 20.0, "R2": 0.0, "R3": -20.0, "R4": FAR}, ("R1", "R3", "TOP", "R2")),
    "D 3 rear wide": ({"R1": 20.0, "R2": 0.0, "R3": -20.0, "R4": FAR}, ("R1", "R3", "R2")),
    "E 3 rear tight": ({"R1": 15.0, "R2": 0.0, "R3": -15.0, "R4": FAR}, ("R1", "R3", "R2")),
    "F 2 rear close": ({"R1": 7.5, "R2": -7.5, "R3": FAR, "R4": FAR}, ("R1", "R2")),
    "G 2 rear apart": ({"R1": 15.0, "R2": -15.0, "R3": FAR, "R4": FAR}, ("R1", "R2")),
}
OBJECTS = {
    "load 30 cm": {"radius": 0.15, "d0": 3.5},
    "pipe 5 cm": {"radius": 0.025, "d0": 3.5},
    "pipe 5 cm, echo only inside 1.5 m": {"radius": 0.025, "d0": 1.5},
}
SPEEDS = (2.0, 2.5, 3.0, 4.0)
BEARINGS = list(range(-45, 46, 5))
T_ON = 3.0


def cfg_for(yaw, **over):
    return {"sensor_yaw_deg": yaw, **over}


def range_stream(order, yaw, scene, traj, duration, half_angle, seed):
    rng = np.random.default_rng(seed)
    u = {k: np.array(v) for k, v in beam_vectors({"sensor_yaw_deg": yaw}).items()}
    sweep = SLOT_S * len(order)
    out = []
    for n in range(math.floor(duration / sweep)):
        for j, sid in enumerate(order):
            t = n * sweep + j * SLOT_S
            r = synth.raycast(traj.pos(t), traj.R(t) @ u[sid], scene, t, half_angle_deg=half_angle)
            if r is not None:
                r = max(0.0, r + rng.normal(0, 0.003))
            out.append(RangeSample(10**9 + round(t * 1e9), sid, None if r is None else round(r * 1000.0)))
    return out


_imu_cache: dict = {}


def imu_stream(duration, seed):
    key = (round(duration * 10), seed)
    if key not in _imu_cache:
        _imu_cache[key] = [
            s for s in synth.simulate(synth.still(tuple(HEAD)), synth.Scene(), duration, synth.SimConfig(seed=seed))
            if not isinstance(s, RangeSample)
        ]
    return _imu_cache[key]


def run_detector(yaw, imu, ranges, **over):
    det = StruckByDetector(cfg_for(yaw, **over))
    stream = sorted(imu + ranges, key=lambda s: s.t_ns)
    return synth.run(det, stream)


def first_warning_s(dets):
    w = [d for d in dets if d.type == "struck_by_warning"]
    return (w[0].t_ns / 1e9 - 1.0) if w else None


def approach(bearing, speed, radius, d0):
    y = math.radians(bearing)
    dirv = np.array([-math.cos(y), math.sin(y), 0.0])
    stop = radius + 0.02

    def centre(t):
        return HEAD + dirv * max(stop, d0 - speed * (t - T_ON))

    t_contact = T_ON + (d0 - radius - HEAD_RADIUS_M) / speed
    return synth.Sphere(centre, radius, T_ON, math.inf), t_contact


def rear_study(half_angle, **over):
    rows = {}
    for lname, (yaw, order) in LAYOUTS.items():
        for oname, obj in OBJECTS.items():
            for speed in SPEEDS:
                recs = []
                for b in BEARINGS:
                    sphere, tc = approach(b, speed, obj["radius"], obj["d0"])
                    dur = tc + 0.6
                    traj = synth.still(tuple(HEAD))
                    rs = range_stream(order, yaw, synth.Scene(spheres=[sphere]), traj, dur, half_angle, seed=b + 100)
                    dets = run_detector(yaw, imu_stream(math.ceil(dur), 1), rs, **over)
                    w = first_warning_s(dets)
                    lead = None if w is None or w > tc else tc - w
                    recs.append({"bearing": b, "lead": lead})
                rows.setdefault(lname, {}).setdefault(oname, {})[speed] = recs
    return rows


def summarize(recs, limit):
    inz = [r for r in recs if abs(r["bearing"]) <= limit]
    leads = [r["lead"] for r in inz if r["lead"] is not None]
    useful = [x for x in leads if x >= USEFUL_LEAD_S]
    return {
        "warned_pct": round(100 * len(leads) / len(inz)),
        "useful_pct": round(100 * len(useful) / len(inz)),
        "median_lead_s": round(st.median(leads), 2) if leads else None,
    }


def _walk():
    """Same setup as tests/detectors/test_struck_by.py: walk backward at 1.2 m/s, stop short of a wall."""
    traj, x_end = walking_backward()
    return traj, x_end - 0.45


def false_alarm_study(seeds=20):
    out = {}
    for lname, (yaw, order) in LAYOUTS.items():
        fa = 0
        for seed in range(seeds):
            traj, wall_x = _walk()
            wall = synth.Scene(planes=[synth.Plane((wall_x, 0, 0), (1, 0, 0))])
            dur = 9.0
            imu = [s for s in synth.simulate(traj, synth.Scene(), dur, synth.SimConfig(seed=seed))
                   if not isinstance(s, RangeSample)]
            rs = range_stream(order, yaw, wall, traj, dur, 7.5, seed)
            dets = run_detector(yaw, imu, rs)
            fa += any(d.type == "struck_by_near_miss" for d in dets)
        out[lname] = {"false_alarm_runs": fa, "of": seeds}
    return out


def falling_study():
    """Gravity drop straight onto the head, TOP sensor only matters. Best-case echo: 20 cm object."""
    out = {}
    for lname in ("A 4 rear + top", "C 3 rear wide + top"):
        yaw, order = LAYOUTS[lname]
        for over_name, over in {"default": {}, "min_track_samples=3": {"min_track_samples": 3}}.items():
            for h in (1.0, 2.0, 3.0, 5.0, 10.0):
                r = 0.1
                t_fall = math.sqrt(2 * max(h - r - HEAD_RADIUS_M, 0.01) / synth.G0)

                def centre(t, h=h):
                    dt = max(0.0, t - T_ON)
                    return HEAD + np.array([0, 0, max(r + 0.02, h - 0.5 * synth.G0 * dt * dt)])

                sphere = synth.Sphere(centre, r, T_ON, math.inf)
                tc = T_ON + t_fall
                dur = tc + 0.6
                leads = []
                for seed in range(5):
                    rs = range_stream(order, yaw, synth.Scene(spheres=[sphere]), synth.still(tuple(HEAD)),
                                      dur, 7.5, seed)
                    dets = run_detector(yaw, imu_stream(math.ceil(dur), seed + 1), rs, **over)
                    w = first_warning_s(dets)
                    leads.append(None if w is None or w > tc else round(tc - w, 2))
                out.setdefault(lname, {}).setdefault(over_name, {})[h] = {
                    "impact_speed_mps": round(synth.G0 * t_fall, 1), "fall_time_s": round(t_fall, 2), "leads_s": leads}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    results = {}
    for tag, ha, over in (("15deg", 7.5, {}), ("15deg_k3", 7.5, {"min_track_samples": 3}), ("30deg", 15.0, {})):
        raw = rear_study(ha, **over)
        results[tag] = {
            l: {o: {str(s): {"p30": summarize(rc, 30), "p45": summarize(rc, 45),
                             "missed_bearings_p30": [r["bearing"] for r in rc
                                                     if abs(r["bearing"]) <= 30 and r["lead"] is None]}
                    for s, rc in sp.items()} for o, sp in ob.items()}
            for l, ob in raw.items()
        }
    results["false_alarms_walk_backward_1.2mps"] = false_alarm_study()
    results["falling"] = falling_study()
    txt = json.dumps(results, indent=1)
    if a.out:
        Path(a.out).write_text(txt)
    for tag in ("15deg", "15deg_k3", "30deg"):
        print(f"\n=== {tag}: within +-30 deg: warned% / useful% (lead>={USEFUL_LEAD_S}s) / median lead s")
        for o in OBJECTS:
            print(f"-- {o}")
            print(f"{'layout':24}" + "".join(f"{s:>16}" for s in SPEEDS))
            for l in LAYOUTS:
                cells = []
                for s in SPEEDS:
                    c = results[tag][l][o][str(s)]["p30"]
                    cells.append(f"{c['warned_pct']:>4}/{c['useful_pct']:>3}/{c['median_lead_s']!s:>5}")
                print(f"{l:24}" + "".join(f"{x:>16}" for x in cells))
    print("\nfalse alarms:", json.dumps(results["false_alarms_walk_backward_1.2mps"]))
    print("\nfalling:", json.dumps(results["falling"], indent=0))


if __name__ == "__main__":
    main()
