"""Typed configuration (spec §5.3 modules, §4 thresholds, §4.4 events). Validated on load."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from argus.contracts import MODULE_NAMES, ImplName

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"
LOCAL_CONFIG_PATH = DEFAULT_CONFIG_PATH.with_name("config.local.yaml")


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
    """Detectors are not swappable: argus/pipeline.py always builds the built-in ones, so anything but
    ``argus`` would be silently ignored. Reject it instead of pretending."""

    impl: ImplName = "argus"

    @field_validator("impl")
    @classmethod
    def _only_argus(cls, v: str) -> str:
        if v != "argus":
            raise ValueError(f"detectors cannot be swapped yet (got {v!r}); only 'argus' is supported. "
                             "Tune thresholds under `thresholds:` instead")
        return v


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
    min_track_samples: int = 3  # spec §3.2a; 4 trades one sweep of warning for steadier tracks
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
    baseline_max_mm: float = 120.0  # head band: stable readings beyond this are the table and never calibrate
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
    status_led_pin: int | None = None  # BCM pin of the optional boot-status LED (17 on the real hat); None = no LED


class BridgeCfg(_Strict):
    """Cloud bridge for the teammates' hat code (argus/bridge/). Paths accept ``~``; blank ones derive from argus_dir."""

    argus_dir: str = "~/argus"  # THEIR tree (hat.main, events.jsonl, clips/). The bridge never writes there.
    python: str = "~/argus/.venv/bin/python"  # their venv python, used to launch `python -m hat.main`
    launch: bool = True  # False = only tail the files (same as `bridge run --no-launch`)
    hat_args: list[str] = Field(default_factory=list)  # extra args for hat.main
    events_path: str = ""  # blank -> <argus_dir>/events.jsonl
    clips_dir: str = ""  # blank -> <argus_dir>/clips
    air_log: str = "~/argus/pi_sensors/logs/air_events.jsonl"
    clip_wait_s: float = 45.0  # how long a fall / line_of_fire waits for its clip before going up without video
    air_max_age_s: float = 300.0  # newest air sample older than this is not attached
    knock_window_s: float = 2.0  # HELMET OFF this soon after a fall = hat_knocked_off (spec D6)
    tof_off_dwell_s: float = 3.0  # their ToF prints OFF this long after the hat left (hat/config.py TOF_OFF_DWELL_MS)
    # NEEDS CONFIRMATION on the real hat: which rear sensor of ours is their left / right ultrasonic.
    # PM decision: their left -> R3 (same pins 23/24), right -> R2; never TOP. needs-teammate: confirm physical sides.
    side_sensor: dict[str, str] = Field(default_factory=lambda: {"left": "R3", "right": "R2"})
    # line_of_fire fires often (473 in ~30 h on their log) and their camera skips a capture while one runs:
    # a line_of_fire this soon after the last queued one is logged, not uploaded.
    lof_min_interval_s: float = 30.0
    # metrics.hat_on is a required boolean. When the hat state is unknown (no `[tof] HELMET` line seen yet):
    # skip = do not upload the event (logged), on / off = use that value.
    hat_on_unknown: str = "skip"
    # Second child: their pi_sensors, SCD41 only (hat.main owns the VL6180X: `--tof-bus` is refused here).
    air_launch: bool = True
    air_python: str = ""  # blank -> <argus_dir>/pi_sensors/.venv/bin/python
    air_args: list[str] = Field(default_factory=lambda: ["--sensors", "scd41", "--scd-bus", "1", "--log-dir", "logs"])
    state_dir: str = "var/bridge"  # saved tail offset + staging for remuxed clips; relative -> under device/


    @field_validator("air_args")
    @classmethod
    def _no_tof(cls, v: list[str]) -> list[str]:
        if any(a.startswith(("--tof-bus", "--bus")) for a in v) or any("tof" in a for a in v if not a.startswith("--")):
            raise ValueError("bridge.air_args must not touch the ToF / I2C bus 3 (hat.main owns the VL6180X)")
        return v

    @field_validator("hat_on_unknown")
    @classmethod
    def _unknown_policy(cls, v: str) -> str:
        if v not in ("skip", "on", "off"):
            raise ValueError("hat_on_unknown must be skip, on or off")
        return v


class ArgusConfig(_Strict):
    modules: ModulesCfg
    detectors: DetectorsCfg = DetectorsCfg()
    thresholds: ThresholdsCfg = ThresholdsCfg()
    events: EventsCfg = EventsCfg()
    bus: BusCfg = BusCfg()
    backend: BackendCfg = BackendCfg()
    device: DeviceCfg = DeviceCfg()
    bridge: BridgeCfg = BridgeCfg()

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


def deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """``over`` wins; nested mappings merge key by key, anything else (lists, scalars) is replaced."""
    out = dict(base)
    for k, v in over.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def local_config_path() -> Path | None:
    """The gitignored per-machine override. ``ARGUS_LOCAL_CONFIG`` moves it; empty or ``none`` turns it off."""
    env = os.environ.get("ARGUS_LOCAL_CONFIG")
    if env is None:
        return LOCAL_CONFIG_PATH
    return None if env.strip().lower() in ("", "none") else Path(env)


def _read_yaml(p: Path, allow_empty: bool = False) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(p.read_text())
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {p}") from None
    except yaml.YAMLError as e:
        raise ConfigError(f"config {p} is not valid YAML: {e}") from None
    if raw is None and allow_empty:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"config {p} must be a YAML mapping at the top level")
    return raw


def load_config(path: Path | str | None = None) -> ArgusConfig:
    """Load and validate config. Precedence, highest first:

    1. ``path`` (``--config``) or ``$ARGUS_CONFIG``: that one file, used as is (the Pi service passes
       ``/etc/argus/config.yaml``). No local override is merged, so what you name is what you get.
    2. Otherwise ``device/config.yaml`` with ``device/config.local.yaml`` (gitignored, optional) deep-merged
       over it. Put machine-specific choices (``impl: team``, a serial port, backend creds) in the local file.
    """
    explicit = path or os.environ.get("ARGUS_CONFIG")
    if explicit:
        p = Path(explicit)
        return parse_config(_read_yaml(p), p)
    raw = _read_yaml(DEFAULT_CONFIG_PATH)
    local = local_config_path()
    if local is not None and local.exists():
        raw = deep_merge(raw, _read_yaml(local, allow_empty=True))
    return parse_config(raw, local if local is not None and local.exists() else DEFAULT_CONFIG_PATH)
