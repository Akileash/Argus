"""Runner equivalence with scenario YAMLs, sweep objective, --apply round trip, CLI smoke."""

from __future__ import annotations

import json
import shutil
import time

import pytest
import yaml
from typer.testing import CliRunner

from argus.cli import app
from argus.config import DEFAULT_CONFIG_PATH, load_config
from argus.tuning.evaluate import evaluate
from argus.tuning.runner import load_thresholds, with_overrides
from argus.tuning.sweep import apply_to_config, grid, parse_param, sweep, yaml_snippet
from hardware.mock.scenario import list_scenarios

runner = CliRunner()

# hat_drop_1m declares a head_impact next to the fall, but fall_impact deliberately reports one
# `fall` for a free-fall landing (test_fall_impact). Scenario YAML and detector disagree; tracked in report.
KNOWN_SCENARIO_MISMATCH = {("hat_drop_1m", "head_impact")}


def test_export_writes_recording_and_labels_for_every_scenario(scenario_dir):
    for name in list_scenarios():
        assert (scenario_dir / f"{name}.jsonl").stat().st_size > 0
        lab = json.loads((scenario_dir / f"{name}.labels.json").read_text())
        assert lab["label"] == name and "expected" in lab


def test_runner_reproduces_scenario_expected_events(scenario_dataset):
    sc = evaluate(scenario_dataset, load_thresholds())
    for r in sc.recordings:
        name = r.name.removesuffix(".jsonl")
        misses = {(name, m.expected.type) for m in r.matches if m.detection is None}
        assert misses <= KNOWN_SCENARIO_MISMATCH, r.name
        assert not r.false_positives, (r.name, r.false_positives)
    assert sc.overall.fp == 0


def test_runner_is_fast(scenario_dataset):
    th = load_thresholds()
    total = sum(r.duration_s for r, _ in scenario_dataset)
    t = time.perf_counter()
    evaluate(scenario_dataset, th)
    wall = time.perf_counter() - t
    assert wall < max(2.0, total / 60.0), f"{wall:.2f}s for {total:.0f}s of data"


def test_with_overrides_validates_keys():
    base = load_thresholds()
    assert with_overrides(base, {"struck_by.ego_tau_s": 2.0})["struck_by"]["ego_tau_s"] == 2.0
    assert base["struck_by"]["ego_tau_s"] == 1.0  # base untouched
    with pytest.raises(KeyError):
        with_overrides(base, {"struck_by.no_such_key": 1})


def test_parse_param_and_grid():
    assert parse_param("struck_by.ego_tau_s=1.0,2") == ("struck_by.ego_tau_s", [1.0, 2])
    assert parse_param("struck_by.ego_compensation=true,false")[1] == [True, False]
    with pytest.raises(ValueError):
        parse_param("nodot=1")
    assert len(grid({"a.x": [1, 2, 3], "a.y": [1, 2]})) == 6


def test_sweep_picks_obviously_better_value(scenario_paths):
    # v_obj_min_mps = 10 can never be satisfied by a 2.5 m/s board: the near miss is missed.
    res = sweep(scenario_paths, load_thresholds(), {"struck_by.v_obj_min_mps": [10.0, 1.5]}, workers=1)
    assert res[0].values == {"struck_by.v_obj_min_mps": 1.5}
    assert res[0].detection_rate > res[1].detection_rate


def test_sweep_false_alarm_budget_outranks_detection_rate(scenario_paths):
    # trigger_range_m=0.3 is infeasible (misses) while the default is feasible; ranking is by budget first.
    res = sweep(scenario_paths, load_thresholds(), {"struck_by.trigger_range_m": [0.3, 2.0]}, workers=1)
    assert res[0].values["struck_by.trigger_range_m"] == 2.0 and res[0].feasible


def test_sweep_ties_prefer_no_change(scenario_paths):
    res = sweep(scenario_paths, load_thresholds(), {"struck_by.haptic_hold_s": [0.5, 0.3]}, workers=1)
    assert res[0].values == {"struck_by.haptic_hold_s": 0.3} and res[0].n_changed == 0


def test_sweep_parallel_matches_serial(scenario_paths):
    params = {"struck_by.ego_tau_s": [1.0, 1.5, 2.0], "struck_by.min_track_samples": [3, 4]}
    base = load_thresholds()
    serial = sweep(scenario_paths, base, params, workers=1)
    par = sweep(scenario_paths, base, params, workers=2)
    assert [(r.values, r.tp, r.fp) for r in serial] == [(r.values, r.tp, r.fp) for r in par]


def test_yaml_snippet_is_loadable():
    snip = yaml_snippet({"struck_by.ego_tau_s": 2.0, "struck_by.min_track_samples": 3, "hat_state.below_min_policy": "clamp"})
    assert yaml.safe_load(snip) == {"thresholds": {"struck_by": {"ego_tau_s": 2.0, "min_track_samples": 3},
                                                   "hat_state": {"below_min_policy": "clamp"}}}


def test_apply_round_trip_changes_only_requested_keys(tmp_path):
    cfg = tmp_path / "config.yaml"
    shutil.copy(DEFAULT_CONFIG_PATH, cfg)
    before = cfg.read_text()
    apply_to_config(cfg, {"struck_by.ego_tau_s": 2.0, "struck_by.min_track_samples": 3,
                          "struck_by.ego_compensation": False})
    after = cfg.read_text()
    changed = [(a, b) for a, b in zip(before.splitlines(), after.splitlines(), strict=True) if a != b]
    assert len(changed) == 3 and len(before.splitlines()) == len(after.splitlines())
    new = load_config(cfg)
    old = load_config(DEFAULT_CONFIG_PATH)
    assert new.thresholds.struck_by.ego_tau_s == 2.0 and new.thresholds.struck_by.min_track_samples == 3
    assert new.thresholds.struck_by.ego_compensation is False
    assert new.thresholds.fall_impact == old.thresholds.fall_impact
    assert new.modules == old.modules
    # comments preserved
    assert "# spec §4.1" in after and after.startswith("# Argus device config")


def test_apply_keeps_inline_comments_and_rejects_unknown(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("thresholds:\n  struck_by:   # note\n    a: 1.0   # keep me\n    b: 2\n  other:\n    a: 9\n")
    apply_to_config(cfg, {"struck_by.a": 1.5})
    assert cfg.read_text() == "thresholds:\n  struck_by:   # note\n    a: 1.5   # keep me\n    b: 2\n  other:\n    a: 9\n"
    with pytest.raises(KeyError):
        apply_to_config(cfg, {"struck_by.zzz": 1})


# ------------------------------------------------------------------ CLI
def test_cli_export_eval_sweep_apply(tmp_path):
    rec = tmp_path / "rec"
    r = runner.invoke(app, ["export-scenarios", "--out", str(rec)])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["eval", "-r", str(rec), "--json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.stdout)
    assert data["overall"]["expected"] >= 5 and data["overall"]["fp"] == 0
    r = runner.invoke(app, ["eval", "-r", str(rec)])
    assert "overall" in r.stdout and "struck_by_near_miss" in r.stdout

    cfg = tmp_path / "config.yaml"
    shutil.copy(DEFAULT_CONFIG_PATH, cfg)
    r = runner.invoke(app, ["sweep", "-r", str(rec), "-c", str(cfg), "--workers", "1",
                            "--param", "struck_by.v_obj_min_mps=10.0,1.5", "--param", "struck_by.min_track_samples=3,4",
                            "--apply"])
    assert r.exit_code == 0, r.output
    assert "thresholds:" in r.stdout and "applied to" in r.stdout
    assert load_config(cfg).thresholds.struck_by.v_obj_min_mps == 1.5


def test_cli_errors(tmp_path):
    assert runner.invoke(app, ["eval", "-r", str(tmp_path / "nope")]).exit_code == 1
    assert runner.invoke(app, ["eval", "-r", str(tmp_path)]).exit_code == 1  # empty dir
    rec = tmp_path / "rec"
    runner.invoke(app, ["export-scenarios", "--out", str(rec)])
    r = runner.invoke(app, ["sweep", "-r", str(rec), "--param", "struck_by.bogus=1,2"])
    assert r.exit_code == 1


def test_unlabelled_recordings_are_skipped(tmp_path):
    rec = tmp_path / "rec"
    runner.invoke(app, ["export-scenarios", "--out", str(rec)])
    (rec / "idle.labels.json").rename(rec / "idle.removed")
    r = runner.invoke(app, ["eval", "-r", str(rec), "--json"])
    assert r.exit_code == 0
    assert len(json.loads(r.stdout)["recordings"]) == len(list_scenarios()) - 1

