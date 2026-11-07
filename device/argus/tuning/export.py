"""Export mock scenarios as ``.jsonl`` recordings plus ``.labels.json`` sidecars."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argus.contracts import ModuleContext
from argus.supervisor import load_impl
from hardware.mock.clock import VirtualClock
from hardware.mock.scenario import list_scenarios, load_scenario
from hardware.replay.recording import write_recording

STEP_S = 0.01


def scenario_labels(name: str) -> dict[str, Any]:
    sc = load_scenario(name)
    expected = []
    for e in sc.expected:
        item: dict[str, Any] = {"type": e.type, "t_s": round((e.t_window_s[0] + e.t_window_s[1]) / 2, 3),
                                "t_window_s": list(e.t_window_s)}
        if e.sensors:
            item["sensors"] = list(e.sensors)
        expected.append(item)
    return {"label": name, "expected": expected, "notes": " ".join(sc.spec.description.split())}


def scenario_samples(name: str) -> list[Any]:
    sc = load_scenario(name)
    clock = VirtualClock()
    ctx = ModuleContext(clock, {}, name)
    mods = [load_impl(m, "mock", ctx) for m in ("imu", "range", "hat_on", "env")]
    for m in mods:
        m.start()
    out: list[Any] = []
    for _ in range(round(sc.duration_s / STEP_S)):
        clock.advance(STEP_S)
        for m in mods:
            out.extend(m.read())
    return out


def export_scenario(name: str, out_dir: Path | str) -> Path:
    out = Path(out_dir)
    path = write_recording(out / f"{name}.jsonl", scenario_samples(name), {"source": f"mock:{name}"})
    (out / f"{name}.labels.json").write_text(json.dumps(scenario_labels(name), indent=2) + "\n")
    return path


def export_all(out_dir: Path | str) -> list[Path]:
    return [export_scenario(n, out_dir) for n in list_scenarios()]
