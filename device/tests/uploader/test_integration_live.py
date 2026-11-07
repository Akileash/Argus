"""Live test against the local stack (Next.js on :3000 + local Supabase). Run with ARGUS_INTEGRATION=1.

Env overrides: ARGUS_BASE_URL (default http://localhost:3000), ARGUS_SUPABASE_URL / ARGUS_SUPABASE_SERVICE_KEY
(default: parsed from ``pnpm exec supabase status -o env`` at the repo root).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from argus.uploader import BackendSettings, Uploader

pytestmark = pytest.mark.skipif(os.environ.get("ARGUS_INTEGRATION") != "1", reason="set ARGUS_INTEGRATION=1")

REPO = Path(__file__).resolve().parents[3]
BASE = os.environ.get("ARGUS_BASE_URL", "http://localhost:3000")
SERIAL, TOKEN = "ARG-0002", "seed-token-ARG-0002"


def _supabase() -> tuple[str, str]:
    url, key = os.environ.get("ARGUS_SUPABASE_URL"), os.environ.get("ARGUS_SUPABASE_SERVICE_KEY")
    if url and key:
        return url, key
    env = {**os.environ, "PATH": "/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:" + os.environ["PATH"]}
    out = subprocess.run(["pnpm", "exec", "supabase", "status", "-o", "env"], cwd=REPO, env=env,
                         capture_output=True, text=True, check=True).stdout
    kv = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    return kv["API_URL"].strip('"'), kv["SERVICE_ROLE_KEY"].strip('"')


def _trace(eid: str) -> bytes:
    """Valid ArgusSensorTrace (contracts/sensor_trace.schema.json), 1 s of idle data."""
    n = 10
    t = [i / 10 - 1.0 for i in range(n)]
    zeros = [0.0] * n
    imu = {"t": t, "a_mag_g": [1.0] * n, "ax": zeros, "ay": zeros, "az": [1.0] * n, "gx": zeros, "gy": zeros,
           "gz": zeros}
    doc = {"schema_version": "1", "event_id": eid, "t_start_s": -1.0, "t_end_s": 0.0, "imu": imu,
           "ranges": {}, "tof": {"t": t, "range_mm": [300.0] * n}, "hat_on": [{"t": -1.0, "on": True}],
           "haptics": [], "detections": [{"t": 0.0, "type": "fall"}]}
    return json.dumps(doc).encode()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")
def test_live_upload_reaches_uploaded(tmp_path: Path):
    try:
        httpx.get(f"{BASE}/api/devices/me", timeout=3)
    except httpx.HTTPError:
        pytest.skip(f"web app not reachable at {BASE}")
    sb_url, sb_key = _supabase()

    eid = str(uuid.uuid4())
    q = tmp_path / "queue"
    d = q / eid
    d.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=15",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(d / "clip.mp4")],
                   check=True)
    clip = (d / "clip.mp4").read_bytes()
    trace = _trace(eid)
    (d / "trace.json").write_bytes(trace)
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    ev = {"schema_version": "1", "event_id": eid, "device_serial": SERIAL, "type": "fall",
          "device_class": "incident", "t0": now, "detected_at": now, "window": {"pre_s": 1, "post_s": 1},
          "metrics": {"peak_g": 9.1, "peak_g_saturated": False, "free_fall_ms": 320, "drop_height_m": 0.5,
                      "hat_on": True},
          "env": {"co2_ppm": 600, "temp_c": 21.0, "rh_pct": 40.0}, "merged_triggers": [],
          "media": [
              {"kind": "clip", "camera": "front", "filename": "clip.mp4", "content_type": "video/mp4",
               "bytes": len(clip), "sha256": hashlib.sha256(clip).hexdigest(), "duration_s": 2.0},
              {"kind": "sensor_trace", "filename": "trace.json", "content_type": "application/json",
               "bytes": len(trace), "sha256": hashlib.sha256(trace).hexdigest()}],
          "firmware": {"version": "integration-test", "impls": {"imu": "mock"}}}
    (d / "event.json").write_text(json.dumps(ev))
    (d / "READY").touch()

    up = Uploader(q, lambda: BackendSettings(BASE, TOKEN, 30.0, 60.0))
    counts = up.drain_once()
    assert counts["uploaded"] == 1, (counts, up.status_line())
    assert not d.exists()

    h = {"apikey": sb_key, "Authorization": f"Bearer {sb_key}"}
    rows = httpx.get(f"{sb_url}/rest/v1/events", params={"id": f"eq.{eid}", "select": "id,status"}, headers=h).json()
    assert rows and rows[0]["status"] in ("uploaded", "analyzing", "ready"), rows
    media = httpx.get(f"{sb_url}/rest/v1/media", params={"event_id": f"eq.{eid}", "select": "filename,confirmed"},
                      headers=h).json()
    assert {m["filename"] for m in media if m["confirmed"]} == {"clip.mp4", "trace.json"}
