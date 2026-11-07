"""Config precedence with the gitignored ``config.local.yaml`` (so teammates never edit the shared config.yaml).

Precedence, highest first: ``--config``/``$ARGUS_CONFIG`` (that one file, used as is), else
``device/config.yaml`` deep-merged with ``device/config.local.yaml``.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest
import yaml
from typer.testing import CliRunner

import argus.config as config_mod
from argus import pairing
from argus.cli import app
from argus.config import DEFAULT_CONFIG_PATH, LOCAL_CONFIG_PATH, deep_merge, load_config

runner = CliRunner()
LOCAL = """
modules:
  range:
    impl: team
    fallback: mock
    options: {mapping_file: /x/range.yaml}
thresholds:
  struck_by: {ttc_max_s: 1.1}
"""


@pytest.fixture
def local(tmp_path, monkeypatch):
    p = tmp_path / "config.local.yaml"
    p.write_text(LOCAL)
    monkeypatch.setenv("ARGUS_LOCAL_CONFIG", str(p))
    monkeypatch.delenv("ARGUS_CONFIG", raising=False)
    return p


def test_local_file_is_deep_merged_over_the_base(local):
    cfg = load_config()
    assert cfg.modules.range.impl == "team" and cfg.modules.range.fallback == "mock"
    assert cfg.modules.range.options["order"] == ["R2", "R3"], "base options survive next to the local ones"
    assert cfg.modules.range.options["mapping_file"] == "/x/range.yaml"
    assert cfg.modules.imu.impl == "mock", "modules the local file does not mention are untouched"
    assert cfg.thresholds.struck_by.ttc_max_s == 1.1 and cfg.thresholds.struck_by.jump_m == 0.35


def test_explicit_config_and_env_config_are_used_as_is(local, tmp_path, monkeypatch):
    shutil.copy(DEFAULT_CONFIG_PATH, tmp_path / "pi.yaml")
    assert load_config(tmp_path / "pi.yaml").modules.range.impl == "mock"  # --config: no local merge
    monkeypatch.setenv("ARGUS_CONFIG", str(tmp_path / "pi.yaml"))
    assert load_config().modules.range.impl == "mock"  # $ARGUS_CONFIG likewise
    assert load_config(DEFAULT_CONFIG_PATH).modules.range.impl == "mock"


def test_missing_empty_or_disabled_local_file_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_CONFIG", raising=False)
    for value in ("none", "", str(tmp_path / "absent.yaml")):
        monkeypatch.setenv("ARGUS_LOCAL_CONFIG", value)
        assert load_config().modules.range.impl == "mock"
    (tmp_path / "empty.yaml").write_text("# only a comment\n")
    monkeypatch.setenv("ARGUS_LOCAL_CONFIG", str(tmp_path / "empty.yaml"))
    assert load_config().modules.range.impl == "mock"


def test_bad_local_file_is_a_clear_config_error(local):
    local.write_text("modules: {range: {impl: banana}}\n")
    with pytest.raises(config_mod.ConfigError, match="modules.range.impl"):
        load_config()


def test_deep_merge_replaces_lists_and_scalars_merges_dicts():
    assert deep_merge({"a": {"b": 1, "c": [1, 2]}, "d": 1}, {"a": {"c": [9]}, "e": 2}) == {
        "a": {"b": 1, "c": [9]}, "d": 1, "e": 2}


def test_default_local_path_is_next_to_config_yaml_and_gitignored():
    assert LOCAL_CONFIG_PATH == DEFAULT_CONFIG_PATH.with_name("config.local.yaml")
    r = subprocess.run(["git", "check-ignore", "-q", str(LOCAL_CONFIG_PATH)], cwd=DEFAULT_CONFIG_PATH.parent,
                       check=False)
    assert r.returncode == 0, "device/config.local.yaml must be in .gitignore"


# ---------------------------------------------------------------- pair / sweep write where people expect
def test_pair_without_config_writes_the_local_file_never_the_shared_one(local, monkeypatch):
    before = DEFAULT_CONFIG_PATH.read_text()
    assert pairing.resolve_config_path(None) == local
    r = runner.invoke(app, ["pair", "--url", "http://b.test", "--token", "tok", "--no-verify"])
    assert r.exit_code == 0 and str(local) in r.output, r.output
    assert yaml.safe_load(local.read_text())["backend"]["device_token"] == "tok"
    assert "range" in yaml.safe_load(local.read_text())["modules"], "existing local settings are kept"
    assert DEFAULT_CONFIG_PATH.read_text() == before
    assert load_config().backend.device_token == "tok"


def test_pair_with_config_or_env_writes_that_file(local, tmp_path, monkeypatch):
    target = tmp_path / "etc-argus.yaml"
    shutil.copy(DEFAULT_CONFIG_PATH, target)
    r = runner.invoke(app, ["pair", "--url", "http://b.test", "--token", "t1", "--no-verify", "--config", str(target)])
    assert r.exit_code == 0 and load_config(target).backend.device_token == "t1"
    assert "backend" not in yaml.safe_load(local.read_text())
    monkeypatch.setenv("ARGUS_CONFIG", str(target))
    assert pairing.resolve_config_path(None) == target


def test_sweep_apply_still_writes_the_shared_config_yaml(tmp_path, monkeypatch):
    """Tuned thresholds belong to everybody, so `sweep --apply` keeps writing config.yaml (or --config)."""
    shared = tmp_path / "config.yaml"
    shutil.copy(DEFAULT_CONFIG_PATH, shared)
    monkeypatch.setattr(config_mod, "DEFAULT_CONFIG_PATH", shared)
    monkeypatch.setenv("ARGUS_LOCAL_CONFIG", "none")
    monkeypatch.delenv("ARGUS_CONFIG", raising=False)
    rec = tmp_path / "rec"
    assert runner.invoke(app, ["export-scenarios", "--out", str(rec)]).exit_code == 0
    r = runner.invoke(app, ["sweep", "-r", str(rec), "--workers", "1", "--param", "struck_by.v_obj_min_mps=10.0,1.5",
                            "--apply"])
    assert r.exit_code == 0 and f"applied to {shared}" in r.output, r.output
    assert load_config(shared).thresholds.struck_by.v_obj_min_mps == 1.5


def test_reload_picks_up_local_config_and_new_options(local, monkeypatch):
    """`argus-cli run` reloads from device/config.yaml + config.local.yaml, options included."""
    from argus.bus import SampleBus
    from argus.contracts import Health, MonotonicClock
    from argus.supervisor import DEFAULT_CONFIG, Supervisor

    seen = {}
    monkeypatch.setattr("argus.supervisor.load_impl", lambda m, i, ctx: seen.update(impl=i, opts=ctx.options)
                        or type("M", (), {"start": lambda s: None, "stop": lambda s: None,
                                                              "health": lambda s: Health(True)})())
    sup = Supervisor(load_config(), SampleBus(), MonotonicClock(), config_path=DEFAULT_CONFIG)
    sup.reload("imu", "mock")
    seen.clear()
    sup.reload("range")
    assert seen["impl"] == "team" and seen["opts"]["mapping_file"] == "/x/range.yaml"
