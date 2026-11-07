"""`systemctl restart` sends SIGTERM: an event in its post-roll must still be flushed to the queue."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

from argus import control
from argus.config import DEFAULT_CONFIG_PATH

DEVICE = Path(__file__).resolve().parents[2]


def test_sigterm_flushes_open_event(tmp_path):
    raw = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text())
    raw["events"]["queue_dir"] = str(tmp_path / "queue")
    raw["modules"]["camera"]["options"]["cache_dir"] = str(tmp_path / "cam")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump(raw))
    sock = os.path.join(tempfile.gettempdir(), f"argus-sigterm-{os.getpid()}.sock")  # AF_UNIX paths must be short
    env = {**os.environ, "ARGUS_CONTROL_SOCK": sock}
    p = subprocess.Popen(
        [sys.executable, "-c", "from argus.cli import app; app()", "run", "--config", str(cfg)],
        cwd=DEVICE, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        resp = None
        for _ in range(100):
            try:
                resp = control.send({"cmd": "trigger", "type": "fall"}, path=sock, timeout=5)
                break
            except (ConnectionError, OSError):
                time.sleep(0.2)
        assert resp and resp["ok"], resp
        time.sleep(1.0)  # well inside the 10 s post-roll
        p.send_signal(signal.SIGTERM)
        out, _ = p.communicate(timeout=30)
    finally:
        if p.poll() is None:
            p.kill()
    assert p.returncode == 0, out
    assert "SIGTERM received" in out
    ready = list((tmp_path / "queue").glob("*/READY"))
    assert len(ready) == 1, out
    assert (ready[0].parent / "event.json").exists()
