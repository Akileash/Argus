"""Static checks for the Pi install artifacts (install.sh, uninstall.sh, argus.service).

These run on any machine. They do not install anything.
"""

import configparser
import os
import shutil
import subprocess
from pathlib import Path

import pytest

DEVICE_DIR = Path(__file__).resolve().parents[2]
SCRIPTS = [DEVICE_DIR / "install.sh", DEVICE_DIR / "uninstall.sh"]
UNIT = DEVICE_DIR / "argus.service"

# Homebrew's prefix is not always on PATH in non-login shells.
_SHELLCHECK_FALLBACKS = ["/opt/homebrew/bin/shellcheck", "/usr/local/bin/shellcheck"]


def _find_shellcheck() -> str | None:
    found = shutil.which("shellcheck")
    if found:
        return found
    for candidate in _SHELLCHECK_FALLBACKS:
        if os.access(candidate, os.X_OK):
            return candidate
    return None


SHELLCHECK = _find_shellcheck()


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_script_exists_and_is_executable(script: Path):
    assert script.is_file(), f"missing {script}"
    assert os.access(script, os.X_OK), f"{script.name} must be executable (chmod +x)"


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_script_bash_syntax(script: Path):
    result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(SHELLCHECK is None, reason="shellcheck not installed")
@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_shellcheck_clean(script: Path):
    result = subprocess.run([SHELLCHECK, str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_install_script_strict_mode_and_pi_guard():
    text = (DEVICE_DIR / "install.sh").read_text()
    assert "set -euo pipefail" in text
    assert "/proc/device-tree/model" in text
    assert "Raspberry Pi" in text
    assert "--force" in text


def test_install_script_uses_apt_picamera2_and_system_site_packages():
    text = (DEVICE_DIR / "install.sh").read_text()
    assert "python3-picamera2" in text
    assert "python3-libcamera" in text
    assert "--system-site-packages" in text
    assert "--extra pi" in text


def test_install_script_substitutes_unit_placeholders():
    text = (DEVICE_DIR / "install.sh").read_text()
    assert "__DEVICE_DIR__" in text
    assert "__ARGUS_USER__" in text
    assert "argus.service" in text


def test_install_script_never_overwrites_config():
    text = (DEVICE_DIR / "install.sh").read_text()
    assert "keeping existing" in text


def _load_unit() -> configparser.ConfigParser:
    parser = configparser.ConfigParser(
        strict=False, interpolation=None, delimiters=("=",)
    )
    parser.optionxform = str  # systemd keys are case-sensitive
    parser.read_string(UNIT.read_text())
    return parser


def test_unit_has_required_sections():
    parser = _load_unit()
    for section in ("Unit", "Service", "Install"):
        assert parser.has_section(section), f"[{section}] missing"


@pytest.mark.parametrize(
    ("section", "key", "expected"),
    [
        ("Unit", "After", "network-online.target"),
        ("Unit", "Wants", "network-online.target"),
        ("Service", "Restart", "always"),
        ("Service", "RestartSec", "2"),
        ("Service", "Nice", "-5"),
        ("Service", "Environment", "PYTHONUNBUFFERED=1"),
        ("Service", "User", "__ARGUS_USER__"),
        ("Service", "WorkingDirectory", "__DEVICE_DIR__"),
        ("Install", "WantedBy", "multi-user.target"),
    ],
)
def test_unit_required_keys(section: str, key: str, expected: str):
    parser = _load_unit()
    actual = parser.get(section, key, fallback="")
    assert actual == expected, f"[{section}] {key}: expected {expected!r}, got {actual!r}"


def test_unit_exec_start_runs_argus_cli_with_config():
    parser = _load_unit()
    exec_start = parser.get("Service", "ExecStart", fallback="")
    assert exec_start.startswith("__DEVICE_DIR__/.venv/bin/argus-cli run")
    assert "--config /etc/argus/config.yaml" in exec_start


def test_unit_has_no_hardcoded_user_or_path():
    text = UNIT.read_text()
    assert "/home/pi" not in text
    assert "User=pi" not in text
