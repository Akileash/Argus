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
    """Spec §4.1. Key names are exactly what argus/detectors/struck_by.py (+ ego_motion, orientation) read.
    tests/unit/test_config_keys.py fails if a key here is not read by a detector."""

    # track
    range_min_m: float = 0.03  # valid range bounds
    range_max_m: float = 2.5
    track_len: int = 4
    min_track_samples: int = 4  # sweep target: 3 buys one sweep of warning time
    jump_m: float = 0.35
    first_step_max_rate_mps: float = 4.0
    handoff_tol_m: float = 0.2
    handoff_max_gap_s: float = 0.25
    track_timeout_s: float = 0.5
    # trigger
    rotation_gate_dps: float = 60.0
    trigger_range_m: float = 2.0
    v_obj_min_mps: float = 1.5
    ttc_max_s: float = 1.3
    confirm_sweeps: int = 2
    suppress_ego_ratio: float = 0.6
    suppress_v_obj_min_mps: float = 2.0
    ego_compensation: bool = True
    ego_discount_only: bool = True
    # events
    approach_gap_s: float = 1.0
    impact_g: float = 6.0
    contact_window_s: float = 1.5
    saturation_g: float = 15.9
    # haptics
    haptic_hold_s: float = 0.3
    haptic_ttc_slow_s: float = 1.3
    haptic_slow_hz: float = 4.0
    haptic_ttc_continuous_s: float = 0.5
    haptic_fast_hz: float = 10.0
    # ego-motion (leaky integrator + ZUPT)
    ego_tau_s: float = 1.0  # sweep target: 2.0 fixes brisk backward walking, doubles drift bound
    zupt_window_s: float = 0.2
    zupt_var_thresh: float = 0.05
    zupt_mean_thresh: float = 0.4
    zupt_max_speed_mps: float = 0.5
    max_dt_s: float = 0.05
    # orientation + boot calibration
    madgwick_beta: float = 0.01
    accel_gate_g: float = 0.25
    calib_window_s: float = 2.0
    calib_gyro_motion_dps: float = 3.0
    calib_accel_motion_g: float = 0.05


class FallImpactThresholds(_Thresholds):
    """Spec §4.2. Keys read by argus/detectors/fall_impact.py."""

    impact_g: float = 6.0
    saturation_g: float = 15.9
    free_fall_g: float = 0.35
    free_fall_min_ms: float = 200.0
    landing_g: float = 3.0
    landing_window_ms: float = 1000.0
    pulse_end_g: float = 3.0
    pulse_hold_ms: float = 30.0
    max_pulse_ms: float = 500.0
    debounce_ms: float = 300.0
    ff_gap_tol_ms: float = 15.0


class HatStateThresholds(_Thresholds):
    """Spec §4.3. Keys read by argus/detectors/hat_state.py."""

    min_range_mm: float = 50.0
    max_range_mm: float = 1200.0
    calibration_s: float = 3.0
    stable_spread_mm: float = 10.0
    off_delta_mm: float = 30.0
    on_band_mm: float = 15.0
    off_confirm_s: float = 3.0
    on_confirm_s: float = 2.0
    knock_lookback_s: float = 2.0
    knock_slack_ms: float = 100.0
    below_min_policy: str = "clamp"


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
    # contracts/device_queue.md. Relative paths resolve against device/. Blank firmware_version -> package default.
    queue_dir: str = "var/queue"
    queue_max_mb: int = 2048
    firmware_version: str = "0.1.0"


class BusCfg(_Strict):
    history_s: float = 35.0


class BackendCfg(_Strict):
    base_url: str = ""  # blank on purpose; filled by `argus-cli pair` / the team
    device_token: str = ""
    timeout_s: float = 30.0  # per HTTP request (uploader / pairing)
    max_backoff_s: float = 60.0  # uploader retry cap (contracts/api.md: 1 s -> 60 s)


class DeviceCfg(_Strict):
    serial: str = ""  # filled by `argus-cli pair` from GET /api/devices/me


class ArgusConfig(_Strict):
    modules: ModulesCfg
    detectors: DetectorsCfg = DetectorsCfg()
    thresholds: ThresholdsCfg = ThresholdsCfg()
    events: EventsCfg = EventsCfg()
    bus: BusCfg = BusCfg()
    backend: BackendCfg = BackendCfg()
    device: DeviceCfg = DeviceCfg()

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
