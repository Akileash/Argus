"""Root pytest config: options for the hardware contract suite (spec §5.6 row 1).

    uv run pytest tests/contract --module imu --impl team       # on the Pi, hardware attached
    uv run pytest tests/contract                                # default: every module, mock impl
"""

from __future__ import annotations

import os

import pytest

# A developer's own config.local.yaml must never change test results; tests that exercise it pass paths explicitly.
os.environ.setdefault("ARGUS_LOCAL_CONFIG", "none")


def pytest_configure(config: pytest.Config) -> None:
    """The unit tests assume "no teammate code is configured". A teammate's committed mapping.yaml must not change
    them, so team mappings are ignored unless a test names one (``mapping_file``) or the contract suite runs
    ``--impl team`` (that is the point of that run)."""
    if "team" not in config.getoption("--impl"):
        os.environ["ARGUS_TEAM_IGNORE_MAPPINGS"] = "1"


def pytest_addoption(parser: pytest.Parser) -> None:
    g = parser.getgroup("argus")
    g.addoption("--module", action="append", default=[], help="contract tests: module(s) to test (default: all)")
    g.addoption("--impl", action="append", default=[], help="contract tests: impl(s) to test (default: mock)")
    g.addoption("--team-mapping", default=None,
                help="contract tests with --impl team: mapping YAML to use instead of the module's mapping.yaml")
