"""Replay engine shared by the replay imu/range/hat_on/env modules (spec §5.3 ``replay``).

All modules replaying the same file share one time origin (``t0``, the clock time of the first
module start), so streams stay synchronised. ``speed`` > 1 compresses time (accelerated replay).
"""

from __future__ import annotations

import bisect
import dataclasses
import logging
import weakref
from pathlib import Path
from typing import Any

from argus.contracts import Clock, Health, ModuleContext
from hardware.replay.recording import STREAM_OF, read_recording

log = logging.getLogger(__name__)
LOOP_GAP_S = 0.1


class _Session:
    def __init__(self, path: Path, clock: Clock) -> None:
        samples = list(read_recording(path))
        if not samples:
            raise ValueError(f"recording {path} has no samples")
        self.base_ns = min(s.t_ns for s in samples)
        self.span_s = (max(s.t_ns for s in samples) - self.base_ns) / 1e9 + LOOP_GAP_S
        self.by_stream: dict[str, list[Any]] = {}
        for s in sorted(samples, key=lambda s: s.t_ns):
            self.by_stream.setdefault(STREAM_OF[type(s)], []).append(s)
        self.t0_ns = clock.now_ns()


_SESSIONS: weakref.WeakKeyDictionary[Clock, dict[str, _Session]] = weakref.WeakKeyDictionary()


def _session(path: Path, clock: Clock) -> _Session:
    d = _SESSIONS.setdefault(clock, {})
    key = str(path.resolve())
    if key not in d:
        d[key] = _Session(path, clock)
    return d[key]


def reset_sessions() -> None:
    _SESSIONS.clear()


class ReplayStream:
    """Replays one stream of a recording. Options: ``file``, ``speed`` (1.0), ``loop`` (False)."""

    stream = ""

    def __init__(self, ctx: ModuleContext) -> None:
        self.clock = ctx.clock
        file = ctx.options.get("file") or ctx.scenario
        if not file or not Path(file).exists():
            raise FileNotFoundError(f"replay needs options.file pointing at a .jsonl recording (got {file!r})")
        self.path = Path(file)
        self.speed = float(ctx.options.get("speed", 1.0))
        self.loop = bool(ctx.options.get("loop", False))
        self._sess: _Session | None = None
        self._items: list[Any] = []
        self._rel: list[float] = []
        self._ptr = self._cycle = 0
        self._running = False
        self._last_t_ns: int | None = None

    def start(self) -> None:
        self._sess = _session(self.path, self.clock)
        self._items = self._sess.by_stream.get(self.stream, [])
        self._rel = [(s.t_ns - self._sess.base_ns) / 1e9 for s in self._items]
        self._ptr = bisect.bisect_left(self._rel, self._now_rec())
        self._cycle = 0
        self._running = True

    def stop(self) -> None:
        self._running = False

    def _now_rec(self) -> float:
        assert self._sess is not None
        return (self.clock.now_ns() - self._sess.t0_ns) / 1e9 * self.speed

    def read(self) -> list[Any]:
        if not self._running or not self._items or self._sess is None:
            return []
        out: list[Any] = []
        now = self._now_rec()
        while True:
            if self._ptr >= len(self._items):
                if not self.loop:
                    break
                self._ptr, self._cycle = 0, self._cycle + 1
            t_rec = self._rel[self._ptr] + self._cycle * self._sess.span_s
            if t_rec > now:
                break
            t_ns = self._sess.t0_ns + int(t_rec / self.speed * 1e9)
            out.append(dataclasses.replace(self._items[self._ptr], t_ns=t_ns))
            self._ptr += 1
        if out:
            self._last_t_ns = out[-1].t_ns
        return out

    def health(self) -> Health:
        age = None if self._last_t_ns is None else (self.clock.now_ns() - self._last_t_ns) / 1e6
        finished = not self.loop and bool(self._items) and self._ptr >= len(self._items)
        note = "recording finished" if finished else ("" if self._running else "not started")
        return Health(self._running and bool(self._items), age, 0, note)
