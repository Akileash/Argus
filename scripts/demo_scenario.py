"""Write a copy of a mock scenario with N seconds of quiet lead-in (every time shifted by N).

Why: the camera ring buffer only holds video since the hat started, so an event 3 s after boot gets a
13 s clip. A real hat has been on for minutes; the demo shifts the scenario so the full 20 s pre-roll exists.
Usage: demo_scenario.py <scenario-name> <lead_in_s> <out.yaml>
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from hardware.mock.scenario import SCENARIO_DIR


def _shift_keys(kf: list, lead: float) -> list:
    if not kf:
        return kf
    out = [[t + lead, v] for t, v in kf]
    return [[0.0, kf[0][1]], *out] if lead > 0 else out


def shift(raw: dict, lead: float) -> dict:
    raw = dict(raw)
    raw["name"] = f"{raw['name']}_lead{lead:g}"
    raw["duration_s"] = raw["duration_s"] + lead
    w = raw.get("wearer") or {}
    for k in ("vx", "vy", "vz", "pitch_deg"):
        if w.get(k):
            w[k] = _shift_keys(w[k], lead)
    if w.get("gait"):
        w["gait"]["start_s"] += lead
        w["gait"]["end_s"] += lead
    for ff in w.get("free_fall", []):
        ff["t"] += lead
    for imp in w.get("impulses", []):
        imp["t"] += lead
    for o in (raw.get("world") or {}).get("objects", []):
        o["t_start"] += lead
    if (raw.get("tof") or {}).get("keyframes"):
        raw["tof"]["keyframes"] = _shift_keys(raw["tof"]["keyframes"], lead)
    for e in raw.get("expected", []):
        e["t_window_s"] = [e["t_window_s"][0] + lead, e["t_window_s"][1] + lead]
    return raw


def main() -> None:
    name, lead, out = sys.argv[1], float(sys.argv[2]), Path(sys.argv[3])
    raw = yaml.safe_load((SCENARIO_DIR / f"{name}.yaml").read_text())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(shift(raw, lead), sort_keys=False))
    print(out)


if __name__ == "__main__":
    main()
