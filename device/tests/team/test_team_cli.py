"""``argus-cli team check`` / ``team init`` on sample output of each fake teammate (no hardware, no processes)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from argus.cli import app

from .conftest import MAPPINGS

runner = CliRunner()
SAMPLES = Path(__file__).parent / "samples"

CASES = [  # module, mapping, sample, expected fragments in the output
    ("imu", "imu", "imu.txt", ["12 readings from 12 lines, 0 errors", "accel g=(+0.020, -0.030, +1.000)",
                               "gyro deg/s=(+1.0, -2.0, +0.5)"]),
    ("range", "range", "range_json.txt", ["14 readings from 14 lines, 0 errors", "R3  855.0 mm", "R2  1200.0 mm"]),
    ("range", "range_text", "range_text.txt", ["20 readings from 10 lines, 0 errors", "R2  1200.0 mm", "R3  855.0 mm"]),
    ("hat_on", "hat_on", "hat_on.txt", ["10 readings from 10 lines, 0 errors", "hat_on ok  63.0 mm"]),
    ("env", "env", "env.txt", ["3 readings from 3 lines, 0 errors", "co2=655.0 ppm", "temp=22.0 C", "rh=41.2 %"]),
]


@pytest.mark.parametrize("module,mapping,sample,expect", CASES)
def test_check_prints_contract_units(module, mapping, sample, expect):
    r = runner.invoke(app, ["team", "check", module, "--sample", str(SAMPLES / sample),
                            "--mapping", str(MAPPINGS / f"{mapping}.yaml")])
    assert r.exit_code == 0, r.output
    for frag in expect:
        assert frag in r.output, f"{frag!r} missing from:\n{r.output}"


def test_check_shows_exact_errors_and_exits_1(tmp_path):
    s = tmp_path / "s.txt"
    s.write_text('{"sensor": "back_left", "dist_cm": 14.8}\nStarting up...\n{"sensor": "front", "dist_cm": 3}\n'
                 '{"sensor": "back_right"}\n{"sensor": "back_right", "dist_cm": 0}\n')
    r = runner.invoke(app, ["team", "check", "range", "-s", str(s), "-m", str(MAPPINGS / "range.yaml")])
    assert r.exit_code == 1
    assert "line 1: range  R2  148.0 mm" in r.output
    assert "line 2: ERROR not JSON: Starting up..." in r.output
    assert "line 3: ERROR sensor 'front' is not mapped to R1..R4/TOP; add it under `ids:`" in r.output
    assert "line 4: ERROR field 'dist_cm' not found" in r.output
    assert "line 5: range  R3  no echo" in r.output
    assert "2 readings from 5 lines, 3 errors" in r.output


def test_check_warns_when_imu_units_look_wrong(tmp_path):
    s = tmp_path / "s.txt"
    s.write_text('{"accel": {"x": 0.0, "y": 0.0, "z": 9.8}, "gyro_rads": [0, 0, 0]}\n' * 3)
    m = tmp_path / "m.yaml"
    m.write_text((MAPPINGS / "imu.yaml").read_text().replace("accel_unit: m/s2", "accel_unit: g"))
    r = runner.invoke(app, ["team", "check", "imu", "-s", str(s), "-m", str(m)])
    assert "median |accel| is 9.80 g" in r.output and "accel_unit" in r.output


def test_check_reads_stdin_and_rejects_bad_mapping(tmp_path):
    r = runner.invoke(app, ["team", "check", "range", "-m", str(MAPPINGS / "range_text.yaml")], input="R2=50 R3=0\n")
    assert r.exit_code == 0 and "R2  500.0 mm" in r.output
    bad = tmp_path / "bad.yaml"
    bad.write_text("unit: cm\n")
    r = runner.invoke(app, ["team", "check", "range", "-m", str(bad)], input="R2=1\n")
    assert r.exit_code == 1 and "mapping problem" in r.output


def test_init_writes_a_loadable_starter_and_refuses_to_overwrite(monkeypatch, tmp_path):
    import hardware.team.imu as imu_pkg
    from hardware.team._mapping import Mapper, load_mapping

    d = tmp_path / "imu"
    d.mkdir()
    monkeypatch.setattr(imu_pkg, "__file__", str(d / "__init__.py"))
    r = runner.invoke(app, ["team", "init", "imu", "--format", "text"])
    assert r.exit_code == 0 and (d / "mapping.yaml").exists()
    m = load_mapping("imu", d, {"mapping_file": str(d / "mapping.yaml")})
    assert m["format"] == "text" and "command" not in m
    Mapper("imu", m)  # the starter is valid as is
    assert runner.invoke(app, ["team", "init", "imu"]).exit_code == 1
    assert runner.invoke(app, ["team", "init", "imu", "--force"]).exit_code == 0
    from hardware.team._starters import starter

    for module in ("range", "hat_on", "env"):
        for fmt in ("json", "text"):
            Mapper(module, yaml.safe_load(starter(module, fmt)))
    for module in ("haptics", "camera"):
        assert yaml.safe_load(starter(module)) in (None, {})  # everything commented out: still "not pasted yet"
