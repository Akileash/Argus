"""Shared helpers for the fake-teammate tests: a config file whose modules point at the fake scripts."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from argus.config import DEFAULT_CONFIG_PATH

HERE = Path(__file__).parent
MAPPINGS = HERE / "mappings"
FAKES = HERE / "fakes"
sys.path.insert(0, str(HERE.parents[1]))  # device/ (so `tests.team.fakes.py_source` imports for python: sources)


def write_config(tmp_path: Path, modules: dict[str, dict[str, Any]] | None = None, **top: Any) -> Path:
    """device/config.yaml (all mocks) with ``modules`` patched, written to ``tmp_path/config.yaml``."""
    raw = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text())
    raw["modules"]["camera"]["options"]["cache_dir"] = str(tmp_path / "camera-cache")
    for name, patch in (modules or {}).items():
        raw["modules"][name].update(patch)
    for k, v in top.items():
        raw[k].update(v) if isinstance(v, dict) else raw.__setitem__(k, v)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw))
    return path


def team(mapping: str, fallback: str | None = "mock", **extra: Any) -> dict[str, Any]:
    """A ``modules.<name>`` entry: impl team using ``tests/team/mappings/<mapping>.yaml``."""
    out: dict[str, Any] = {"impl": "team", "options": {"mapping_file": str(MAPPINGS / f"{mapping}.yaml"), **extra}}
    if fallback:
        out["fallback"] = fallback
    return out


@pytest.fixture
def cfg_file(tmp_path: Path):
    return lambda modules=None, **top: write_config(tmp_path, modules, **top)
