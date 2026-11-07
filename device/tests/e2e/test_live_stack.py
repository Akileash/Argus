"""Live-stack end-to-end: runs scripts/demo-e2e.sh (local Supabase + web + worker + mock hat).

Gated: needs Docker, local Supabase, node/pnpm, ffmpeg. Run with ARGUS_INTEGRATION=1.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "demo-e2e.sh"

pytestmark = pytest.mark.skipif(os.environ.get("ARGUS_INTEGRATION") != "1", reason="set ARGUS_INTEGRATION=1")


@pytest.mark.timeout(900)
def test_demo_e2e_script_passes():
    r = subprocess.run([str(SCRIPT)], capture_output=True, text=True, timeout=880)
    assert r.returncode == 0, f"demo-e2e.sh failed ({r.returncode})\n{r.stdout[-6000:]}\n{r.stderr[-2000:]}"
    assert "RESULT: PASS" in r.stdout
