"""Root pytest config: options for the hardware contract suite (spec §5.6 row 1).

    uv run pytest tests/contract --module imu --impl team       # on the Pi, hardware attached
    uv run pytest tests/contract                                # default: every module, mock impl
"""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    g = parser.getgroup("argus")
    g.addoption("--module", action="append", default=[], help="contract tests: module(s) to test (default: all)")
    g.addoption("--impl", action="append", default=[], help="contract tests: impl(s) to test (default: mock)")
