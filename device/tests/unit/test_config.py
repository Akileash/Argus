import copy

import pytest
import yaml

from argus.config import DEFAULT_CONFIG_PATH, ConfigError, load_config, parse_config
from argus.contracts import MODULE_NAMES


def raw() -> dict:
    return yaml.safe_load(DEFAULT_CONFIG_PATH.read_text())


def test_default_config_loads_with_every_module():
    cfg = load_config()
    for m in MODULE_NAMES:
        assert cfg.modules[m].impl == "mock"
    assert cfg.detectors.struck_by.impl == "argus"


def test_spec_numbers():
    t = load_config().thresholds
    s = t.struck_by
    assert (s.range_max_m, s.v_obj_min_mps, s.ttc_max_s, s.confirm_sweeps) == (2.0, 1.5, 1.3, 2)
    assert (s.rotation_gate_dps, s.suppress_ratio, s.suppress_v_obj_mps) == (60, 0.6, 2.0)
    assert (s.track_jump_m, s.handoff_m, s.range_min_valid_m, s.range_max_valid_m) == (0.35, 0.2, 0.03, 2.5)
    assert (s.leaky_tau_s, s.zupt_ms, s.track_len) == (1.0, 200, 4)
    f = t.fall_impact
    assert (f.impact_g, f.free_fall_g, f.free_fall_min_ms, f.post_fall_impact_g, f.post_fall_window_s) == (
        6.0, 0.35, 200, 3.0, 1.0)
    h = t.hat_state
    assert (h.off_delta_mm, h.off_hold_s, h.on_band_mm, h.on_hold_s, h.knocked_lookback_s, h.knocked_impulse_g) == (
        30, 3.0, 15, 2.0, 2.0, 3.0)
    e = load_config().events
    assert (e.pre_roll_s, e.post_roll_s, e.merge_extend_max_s, e.cooldown_s) == (20, 10, 10, 5)
    assert load_config().bus.history_s == 35


def test_backend_blank_and_no_rear_camera():
    cfg = load_config()
    assert cfg.backend.base_url == "" and cfg.backend.device_token == ""
    assert "rear" not in cfg.modules.camera.options
    assert "rear" not in raw()["modules"]["camera"]


def test_detector_cfg_is_plain_dict():
    d = load_config().detector_cfg("struck_by")
    assert d["v_obj_min_mps"] == 1.5 and isinstance(d, dict)


def test_fallback_and_options_parse():
    r = raw()
    r["modules"]["imu"] = {"impl": "argus", "fallback": "team", "options": {"rate_hz": 800}}
    cfg = parse_config(r)
    assert cfg.modules.imu.fallback == "team" and cfg.modules.imu.options == {"rate_hz": 800}


def test_unknown_impl_error_is_clear():
    r = raw()
    r["modules"]["imu"]["impl"] = "magic"
    with pytest.raises(ConfigError, match=r"modules\.imu\.impl"):
        parse_config(r)


def test_missing_module_and_typos_rejected():
    r = raw()
    del r["modules"]["env"]
    with pytest.raises(ConfigError, match=r"modules\.env"):
        parse_config(r)
    r = raw()
    r["thresholds"]["fall_impact"]["impact_g"] = "lots"
    with pytest.raises(ConfigError, match="impact_g"):
        parse_config(r)
    r = raw()
    r["evnts"] = {}
    with pytest.raises(ConfigError, match="evnts"):
        parse_config(r)


def test_extra_threshold_keys_allowed_for_detector_authors():
    r = copy.deepcopy(raw())
    r["thresholds"]["struck_by"]["my_new_knob"] = 3
    assert parse_config(r).detector_cfg("struck_by")["my_new_knob"] == 3


def test_load_errors(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("modules: [unclosed")
    with pytest.raises(ConfigError, match="YAML"):
        load_config(bad)
    bad.write_text("- a list")
    with pytest.raises(ConfigError, match="mapping"):
        load_config(bad)
