from __future__ import annotations

from pathlib import Path

import httpx
import yaml
from typer.testing import CliRunner

from argus import cli, pairing
from argus.config import DEFAULT_CONFIG_PATH, load_config

ME = {"device_id": "d", "serial": "ARG-0002", "org_id": "o", "assigned_worker": {"id": "w", "full_name": "Ada Lovelace"},
      "server_time": "2026-01-01T00:00:00Z"}


def _cfg(tmp_path: Path) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(DEFAULT_CONFIG_PATH.read_text())
    return p


def test_write_pairing_preserves_comments_and_keys(tmp_path):
    p = _cfg(tmp_path)
    pairing.write_pairing(p, "http://x.test/", "tok", "ARG-0002")
    text = p.read_text()
    assert "# Argus device config." in text and "# blank on purpose" in text or "backend:" in text
    assert "impl: mock" in text
    cfg = load_config(p)
    assert cfg.backend.base_url == "http://x.test" and cfg.backend.device_token == "tok"
    assert cfg.device.serial == "ARG-0002"
    assert cfg.thresholds.fall_impact.impact_g == 6.0
    assert yaml.safe_load(text)["modules"]["camera"]["options"]["ring_s"] == 35


def test_write_pairing_adds_missing_sections(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("# hi\nmodules: {}\n")
    pairing.write_pairing(p, "http://x", "t", "S")
    d = yaml.safe_load(p.read_text())
    assert d["backend"]["device_token"] == "t" and d["device"]["serial"] == "S"
    assert p.read_text().startswith("# hi")


def test_fetch_me_errors():
    ok = httpx.MockTransport(lambda r: httpx.Response(200, json=ME))
    assert pairing.fetch_me("http://x", "t", transport=ok)["serial"] == "ARG-0002"
    bad = httpx.MockTransport(lambda r: httpx.Response(401, json={}))
    try:
        pairing.fetch_me("http://x", "t", transport=bad)
        raise AssertionError
    except pairing.PairingError as e:
        assert "401" in str(e)


def test_cli_pair_and_check(tmp_path, monkeypatch):
    p = _cfg(tmp_path)
    seen = {}

    def fake_fetch(url, token, **kw):
        seen["args"] = (url, token)
        return ME

    monkeypatch.setattr(pairing, "fetch_me", fake_fetch)
    r = CliRunner().invoke(cli.app, ["pair", "--url", "http://b.test", "--token", "abc", "--config", str(p)])
    assert r.exit_code == 0, r.output
    assert "ARG-0002" in r.output and "Ada Lovelace" in r.output
    assert load_config(p).backend.device_token == "abc"
    r = CliRunner().invoke(cli.app, ["pair", "--check", "--config", str(p)])
    assert r.exit_code == 0 and "ARG-0002" in r.output
    assert seen["args"] == ("http://b.test", "abc")


def test_cli_pair_bad_token_writes_nothing(tmp_path, monkeypatch):
    p = _cfg(tmp_path)

    def boom(*a, **k):
        raise pairing.PairingError("backend rejected the device token (401)")

    monkeypatch.setattr(pairing, "fetch_me", boom)
    r = CliRunner().invoke(cli.app, ["pair", "--url", "http://b", "--token", "x", "--config", str(p)])
    assert r.exit_code == 1
    assert load_config(p).backend.device_token == ""


def test_cli_check_unpaired_fails(tmp_path):
    r = CliRunner().invoke(cli.app, ["pair", "--check", "--config", str(_cfg(tmp_path))])
    assert r.exit_code == 1


def test_cli_upload_status(tmp_path):
    r = CliRunner().invoke(cli.app, ["upload", "--status", "--config", str(_cfg(tmp_path)),
                                     "--queue-dir", str(tmp_path / "nope")])
    assert r.exit_code == 0 and "queue_depth=0" in r.output and "paired=no" in r.output


def test_new_local_config_is_0600_and_existing_mode_kept(tmp_path):
    import os
    import stat

    new = tmp_path / "config.local.yaml"
    pairing.write_pairing(new, "http://x", "tok", None)
    assert stat.S_IMODE(os.stat(new).st_mode) == 0o600
    old = tmp_path / "old.yaml"
    old.write_text("modules: {}\n")
    os.chmod(old, 0o640)
    pairing.write_pairing(old, "http://x", "tok", None)
    assert stat.S_IMODE(os.stat(old).st_mode) == 0o640
    os.chmod(old, 0o600)
    pairing.write_pairing(old, "http://x", "tok2", None)
    assert stat.S_IMODE(os.stat(old).st_mode) == 0o600


def test_clock_warning_thresholds():
    me = {"server_time": "2026-01-01T00:00:00Z"}
    base = 1767225600.0  # 2026-01-01T00:00:00Z
    assert pairing.clock_warning(me, base + 30) is None
    w = pairing.clock_warning(me, base + 3600)
    assert w and "ahead of" in w and "timedatectl" in w
    assert "behind" in pairing.clock_warning(me, base - 600)
    assert pairing.clock_warning({}, base) is None


def test_cli_pair_warns_on_skewed_clock(tmp_path, monkeypatch):
    p = _cfg(tmp_path)
    monkeypatch.setattr(pairing, "fetch_me", lambda *a, **k: {**ME, "server_time": "2001-01-01T00:00:00Z"})
    r = CliRunner().invoke(cli.app, ["pair", "--url", "http://x", "--token", "t", "--config", str(p)])
    assert r.exit_code == 0 and "device clock" in r.output and "timedatectl" in r.output
    r = CliRunner().invoke(cli.app, ["pair", "--check", "--config", str(p)])
    assert r.exit_code == 0 and "device clock" in r.output


def test_uploader_warns_once_on_skewed_clock(tmp_path, caplog):
    from argus.uploader import BackendSettings, Uploader

    tr = httpx.MockTransport(lambda r: httpx.Response(200, json={**ME, "server_time": "2001-01-01T00:00:00Z"}))
    u = Uploader(tmp_path, lambda: BackendSettings("http://x", "tok"), transport=tr)
    with caplog.at_level("WARNING"):
        u._check_clock()
        u._check_clock()
    assert sum("device clock" in r.message for r in caplog.records) == 1
