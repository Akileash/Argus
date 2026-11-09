"""Static checks for the bridge's Pi install artifacts. Nothing is installed."""

from __future__ import annotations

import configparser
import os
import shutil
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
SCRIPT = DEPLOY / "install-bridge.sh"
UNIT = DEPLOY / "argus-bridge.service"


def test_script_is_executable_and_documents_itself():
    assert os.access(SCRIPT, os.X_OK)
    head = "\n".join(SCRIPT.read_text().splitlines()[:25])
    assert "~/argus-cloud" in head and "does NOT edit ~/argus" in head


def test_script_targets_argus_cloud_and_refuses_their_tree():
    s = SCRIPT.read_text()
    assert 'CLOUD_DIR="${HOME}/argus-cloud"' in s and 'THEIR_DIR="${HOME}/argus"' in s
    assert "uv sync --locked --no-dev --python" in s and "--extra pi" not in s  # no hardware libraries
    assert "astral.sh/uv/install.sh" in s  # the Pi has no uv


def test_unit_runs_the_bridge_from_the_cloud_venv_and_kills_the_group():
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.read_string(UNIT.read_text())
    svc = cp["Service"]
    assert svc["ExecStart"] == "__DEVICE_DIR__/.venv/bin/argus-cli bridge run --config /etc/argus/config.yaml"
    assert svc["KillMode"] == "control-group" and svc["Restart"] == "always"
    assert "argus-sensors" in UNIT.read_text()  # the warning about their unit is in the header


@pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck not installed")
def test_shellcheck():
    r = subprocess.run(["shellcheck", str(SCRIPT)], capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stdout
