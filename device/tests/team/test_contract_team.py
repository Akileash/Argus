"""The hardware contract suite (spec §5.6 row 1) run with ``--impl team`` against each fake teammate script.

These are the exact commands a teammate runs on the Pi. The suite runs once per module in a child pytest (in
parallel, they mostly sleep); env skips the two 30 s window tests (rate, units) to keep this under a minute.
"""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from .conftest import MAPPINGS

DEVICE = Path(__file__).parents[2]
RUNS = {
    "imu": ("imu", MAPPINGS / "imu.yaml", []),
    "range-json": ("range", MAPPINGS / "range.yaml", []),
    "range-text": ("range", MAPPINGS / "range_text.yaml", []),
    "hat_on": ("hat_on", MAPPINGS / "hat_on.yaml", []),
    "env": ("env", MAPPINGS / "env.yaml", ["-k", "not rate_within and not units_in_range"]),
    "haptics": ("haptics", MAPPINGS / "haptics.yaml", []),
}


def run_one(item):
    module, mapping, extra = item
    cmd = [sys.executable, "-m", "pytest", "tests/contract", "--module", module, "--impl", "team",
           "--team-mapping", str(mapping), "-q", "-p", "no:cacheprovider", *extra]
    return subprocess.run(cmd, cwd=DEVICE, capture_output=True, text=True, timeout=300, check=False,
                          env={k: v for k, v in os.environ.items() if k != "ARGUS_TEAM_IGNORE_MAPPINGS"})


@pytest.fixture(scope="module")
def results():
    with ThreadPoolExecutor(len(RUNS)) as ex:
        return dict(zip(RUNS, ex.map(run_one, RUNS.values()), strict=True))


@pytest.mark.timeout(400)
@pytest.mark.parametrize("name", list(RUNS))
def test_contract_suite_passes_against_fake_teammate(name, results):
    r = results[name]
    assert r.returncode == 0, f"{name}:\n{r.stdout[-3000:]}\n{r.stderr[-1000:]}"
    assert " passed" in r.stdout and " failed" not in r.stdout
