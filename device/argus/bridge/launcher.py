"""Run the teammates' ``python -m hat.main`` as a child in its own process group.

Every console line is passed through unchanged (to stdout, i.e. the journal). ``[tof] HELMET ON|OFF`` lines are
parsed and handed to ``on_tof``. If the child exits it is restarted with backoff; ``stop()`` terminates the whole
group (SIGTERM, then SIGKILL) so nothing is left holding the I2C buses or the camera.
"""

from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("argus.bridge.launcher")

# Their format: f"[tof] HELMET {'ON' if worn else 'OFF'}  mm={snap.distance_mm} near={snap.near}"  (hat/main.py)
TOF_RE = re.compile(r"\[tof\] HELMET (ON|OFF)\b(?:\s+mm=(\S+))?(?:\s+near=(\S+))?")


@dataclass(frozen=True)
class TofLine:
    on: bool
    mm: str | None
    near: str | None


def parse_tof(line: str) -> TofLine | None:
    """Parse one console line (a ``\\r``-joined progress line may hold several statements; the last tof wins)."""
    hit = None
    for part in line.replace("\r", "\n").splitlines():
        m = TOF_RE.search(part)
        if m:
            hit = TofLine(m.group(1) == "ON", m.group(2), m.group(3))
    return hit


class HatLauncher:
    def __init__(self, cmd: list[str], cwd: Path, on_tof: Callable[[TofLine], None], *,
                 out: Callable[[str], None] | None = None, backoff_s: tuple[float, float] = (1.0, 30.0),
                 healthy_after_s: float = 60.0, kill_after_s: float = 10.0) -> None:
        self.cmd, self.cwd, self.on_tof = cmd, cwd, on_tof
        self.out = out or (lambda s: (sys.stdout.write(s), sys.stdout.flush()))
        self.backoff_s, self.healthy_after_s, self.kill_after_s = backoff_s, healthy_after_s, kill_after_s
        self._stop = threading.Event()
        self._proc: subprocess.Popen[bytes] | None = None
        self._thread: threading.Thread | None = None
        self.starts = 0
        self.exits: list[int] = []

    def start(self) -> None:
        self._thread = threading.Thread(target=self._supervise, name="hat-launcher", daemon=True)
        self._thread.start()

    def _spawn(self) -> subprocess.Popen[bytes]:
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        p = subprocess.Popen(self.cmd, cwd=self.cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                             start_new_session=True)  # own process group: killpg reaches anything it spawned
        self.starts += 1
        log.info("started hat.main pid %d: %s (cwd %s)", p.pid, " ".join(self.cmd), self.cwd)
        return p

    def _pump(self, p: subprocess.Popen[bytes]) -> None:
        assert p.stdout is not None
        for raw in iter(p.stdout.readline, b""):
            line = raw.decode("utf-8", "replace")
            self.out(line)
            try:
                t = parse_tof(line)
                if t:
                    self.on_tof(t)
            except Exception:
                log.exception("tof handler failed")

    def _supervise(self) -> None:
        delay = self.backoff_s[0]
        while not self._stop.is_set():
            t_start = time.monotonic()
            try:
                self._proc = p = self._spawn()
            except OSError as e:
                log.error("cannot start %s: %s", self.cmd, e)
                p = None
            if p is not None:
                self._pump(p)
                rc = p.wait()
                self.exits.append(rc)
                if self._stop.is_set():
                    return
                log.error("hat.main exited with code %s; restarting in %.0fs", rc, delay)
            if time.monotonic() - t_start >= self.healthy_after_s:
                delay = self.backoff_s[0]
            if self._stop.wait(delay):
                return
            delay = min(delay * 2, self.backoff_s[1])

    def stop(self) -> None:
        self._stop.set()
        p = self._proc
        if p is not None and p.poll() is None:
            self._signal(p, signal.SIGTERM)
            try:
                p.wait(self.kill_after_s)
            except subprocess.TimeoutExpired:
                log.warning("hat.main ignored SIGTERM for %.0fs; killing its process group", self.kill_after_s)
                self._signal(p, signal.SIGKILL)
                p.wait()
        if self._thread:
            self._thread.join(timeout=5)

    @staticmethod
    def _signal(p: subprocess.Popen[bytes], sig: int) -> None:
        try:
            os.killpg(os.getpgid(p.pid), sig)
        except (ProcessLookupError, PermissionError):
            pass
