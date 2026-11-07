"""Run the detectors over a recording, offline and single-threaded (no supervisor, no bus).

Wiring mirrors the runtime: the fall/impact detector is hat_state's ``impact_source`` and
``DetectorState.hat_on`` follows the hat_state detector.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from argus.config import load_config
from argus.contracts import EnvSample, ImuSample, RangeSample, TofSample
from argus.detectors.base import Detection, DetectorState
from argus.detectors.fall_impact import FallImpactDetector
from argus.detectors.hat_state import HatStateDetector
from argus.detectors.struck_by import StruckByDetector
from hardware.replay.recording import read_recording

Thresholds = dict[str, dict[str, Any]]
SECTIONS = ("struck_by", "fall_impact", "hat_state")
_ORDER = {ImuSample: 0, RangeSample: 1, TofSample: 2, EnvSample: 3}


@dataclass(frozen=True)
class Recording:
    path: Path
    samples: tuple[Any, ...]  # sorted by t_ns
    t0_ns: int
    duration_s: float

    @property
    def name(self) -> str:
        return self.path.name


def load_recording(path: Path | str) -> Recording:
    """Read a ``.jsonl`` recording; the time axis is ``t_ns - min(t_ns)`` (same as replay)."""
    samples = sorted(read_recording(path), key=lambda s: (s.t_ns, _ORDER[type(s)]))
    if not samples:
        return Recording(Path(path), (), 0, 0.0)
    t0 = samples[0].t_ns
    return Recording(Path(path), tuple(samples), t0, (samples[-1].t_ns - t0) / 1e9)


def load_thresholds(config_path: Path | str | None = None) -> Thresholds:
    """``thresholds`` of config.yaml (defaults filled in) as plain dicts, one per detector."""
    cfg = load_config(config_path)
    return {s: cfg.detector_cfg(s) for s in SECTIONS}


def with_overrides(base: Thresholds, overrides: dict[str, Any]) -> Thresholds:
    """Copy of ``base`` with dotted keys (``struck_by.ego_tau_s``) replaced. Unknown keys raise."""
    out = {s: dict(v) for s, v in base.items()}
    for dotted, value in overrides.items():
        section, _, key = dotted.partition(".")
        if section not in out or key not in out[section]:
            raise KeyError(f"unknown threshold {dotted!r}")
        out[section][key] = value
    return out


def run_detectors(samples: Sequence[Any], thresholds: Thresholds) -> list[Detection]:
    """Feed time-ordered samples to all three detectors; return every Detection in emit order."""
    fall = FallImpactDetector(thresholds.get("fall_impact"))
    hat = HatStateDetector(thresholds.get("hat_state"), impact_source=fall)
    sb = StruckByDetector(thresholds.get("struck_by"))
    state = DetectorState(hat_on=hat.hat_on)
    out: list[Detection] = []
    for s in samples:
        if isinstance(s, ImuSample):
            out += fall.on_imu(s, state)
            out += hat.on_imu(s, state)
            out += sb.on_imu(s, state)
        elif isinstance(s, RangeSample):
            out += sb.on_range(s, state)
        elif isinstance(s, TofSample):
            out += hat.on_tof(s, state)
            state.hat_on = hat.hat_on
            out += sb.on_tof(s, state)
        elif isinstance(s, EnvSample):
            out += sb.on_env(s, state)
    return out


def run_recording(rec: Recording, thresholds: Thresholds) -> list[Detection]:
    return run_detectors(rec.samples, thresholds)
