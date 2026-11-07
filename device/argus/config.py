"""Typed configuration (spec §5.3 modules, §4 thresholds, §4.4 events). Validated on load."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from argus.contracts import MODULE_NAMES, ImplName

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"


class ConfigError(ValueError):
    """Raised with a human-readable message when config.yaml is invalid."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModuleCfg(_Strict):
    impl: ImplName
    fallback: ImplName | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class ModulesCfg(_Strict):
    imu: ModuleCfg
    range: ModuleCfg
    hat_on: ModuleCfg
    env: ModuleCfg
    haptics: ModuleCfg
    camera: ModuleCfg  # camera.rear intentionally absent: rear camera is skipped (build plan §1)

    def __getitem__(self, name: str) -> ModuleCfg:
        if name not in MODULE_NAMES:
            raise KeyError(f"unknown module {name!r}; known: {list(MODULE_NAMES)}")
        return getattr(self, name)


class DetectorCfg(_Strict):
    impl: ImplName = "argus"


class DetectorsCfg(_Strict):
    struck_by: DetectorCfg = DetectorCfg()
    fall_impact: DetectorCfg = DetectorCfg()
    hat_state: DetectorCfg = DetectorCfg()


class _Thresholds(BaseModel):
    # extra="allow": detector authors may add tuning keys without touching this file.
    model_config = ConfigDict(extra="allow")


class StruckByThresholds(_Thresholds):
    """Spec §4.1."""

    range_max_m: float = 2.0  # trigger only if range < this
    v_obj_min_mps: float = 1.5
    ttc_max_s: float = 1.3
    confirm_sweeps: int = 2
    rotation_gate_dps: float = 60.0
    suppress_ratio: float = 0.6  # v_ego > ratio * v_meas ...
    suppress_v_obj_mps: float = 2.0  # ... then require v_obj > this
    track_len: int = 4
    track_jump_m: float = 0.35
    handoff_m: float = 0.2
    range_min_valid_m: float = 0.03
    range_max_valid_m: float = 2.5
    leaky_tau_s: float = 1.0
    zupt_ms: float = 200.0
    zupt_accel_var_g2: float = 4e-4  # not in spec: "accel variance below a threshold"; tune offline


class FallImpactThresholds(_Thresholds):
    """Spec §4.2."""

    impact_g: float = 6.0
    saturation_g: float = 16.0
    free_fall_g: float = 0.35
    free_fall_min_ms: float = 200.0
    post_fall_impact_g: float = 3.0
    post_fall_window_s: float = 1.0
    struck_by_upgrade_window_s: float = 1.5


class HatStateThresholds(_Thresholds):
    """Spec §4.3."""

    off_delta_mm: float = 30.0
    off_hold_s: float = 3.0
    on_band_mm: float = 15.0
    on_hold_s: float = 2.0
    knocked_lookback_s: float = 2.0
    knocked_impulse_g: float = 3.0
    baseline_s: float = 3.0
    calibration_s: float = 2.0


class ThresholdsCfg(_Strict):
    struck_by: StruckByThresholds = StruckByThresholds()
    fall_impact: FallImpactThresholds = FallImpactThresholds()
    hat_state: HatStateThresholds = HatStateThresholds()


class EventsCfg(_Strict):
    """Spec §4.4."""

    pre_roll_s: float = 20.0
    post_roll_s: float = 10.0
    merge_extend_max_s: float = 10.0
    cooldown_s: float = 5.0


class BusCfg(_Strict):
    history_s: float = 35.0


class BackendCfg(_Strict):
    base_url: str = ""  # blank on purpose; filled by `argus-cli pair` / the team
    device_token: str = ""


class ArgusConfig(_Strict):
    modules: ModulesCfg
    detectors: DetectorsCfg = DetectorsCfg()
    thresholds: ThresholdsCfg = ThresholdsCfg()
    events: EventsCfg = EventsCfg()
    bus: BusCfg = BusCfg()
    backend: BackendCfg = BackendCfg()

    def detector_cfg(self, name: str) -> dict[str, Any]:
        """``thresholds.<detector>`` as a plain dict, the ``cfg`` a Detector is constructed with."""
        return getattr(self.thresholds, name).model_dump()


def _format_errors(path: Path, err: ValidationError) -> str:
    lines = [f"invalid config {path}:"]
    for e in err.errors():
        loc = ".".join(str(p) for p in e["loc"]) or "<root>"
        lines.append(f"  {loc}: {e['msg']}")
    return "\n".join(lines)


def parse_config(data: dict[str, Any], path: Path | str = "<memory>") -> ArgusConfig:
    try:
        return ArgusConfig.model_validate(data)
    except ValidationError as e:
        raise ConfigError(_format_errors(Path(path), e)) from None


def load_config(path: Path | str | None = None) -> ArgusConfig:
    """Load and validate config. Order: explicit path, $ARGUS_CONFIG, ``device/config.yaml``."""
    p = Path(path or os.environ.get("ARGUS_CONFIG") or DEFAULT_CONFIG_PATH)
    try:
        raw = yaml.safe_load(p.read_text())
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {p}") from None
    except yaml.YAMLError as e:
        raise ConfigError(f"config {p} is not valid YAML: {e}") from None
    if not isinstance(raw, dict):
        raise ConfigError(f"config {p} must be a YAML mapping at the top level")
    return parse_config(raw, p)
