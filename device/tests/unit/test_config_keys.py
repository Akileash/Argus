"""Drift guard: every threshold key in config.yaml must be read by its detector code.

Prevents the silent-mismatch bug where config says one name and the detector reads another
(and quietly falls back to its own default).
"""

import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCES = {
    "struck_by": ["struck_by.py", "ego_motion.py", "orientation.py", "geometry.py"],
    "fall_impact": ["fall_impact.py"],
    "hat_state": ["hat_state.py"],
}


def test_every_config_threshold_is_read_by_a_detector():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())["thresholds"]
    missing = []
    for section, files in SOURCES.items():
        src = "".join((ROOT / "argus" / "detectors" / f).read_text() for f in files)
        for key in cfg[section]:
            if f'"{key}"' not in src:
                missing.append(f"{section}.{key}")
    assert not missing, f"config keys not read by any detector: {missing}"
