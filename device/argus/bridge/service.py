"""The bridge: tail their files, wait for clips, map, and write into OUR queue (the uploader sends from there)."""

from __future__ import annotations

import json
import logging
import shutil
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from argus.bridge import mapper
from argus.bridge.air import latest_air
from argus.bridge.clips import ClipPolicy, probe_duration_s, wait_for_clip
from argus.bridge.launcher import HatLauncher, TofLine, parse_tof
from argus.bridge.tail import EventsTailer, StateFile
from argus.config import ArgusConfig
from argus.events.queue import DEVICE_DIR, EventQueue, SchemaError, validate_trace

log = logging.getLogger("argus.bridge")
ISSUE = "INTEGRATION ISSUE"  # grep-able marker in the journal for things the PR should raise
DEFAULT_CLIP_S = mapper.CLIP_PRE_S + mapper.CLIP_POST_S
KEEP_S = 600.0  # how long a fall stays eligible for a hat_knocked_off link / shared clip
TOF_SETTLE_S = 0.5  # decide a HELMET OFF this long after it arrives (the events file is polled first)


def expand(p: str) -> Path:
    return Path(p).expanduser()


def resolve_state_dir(p: str) -> Path:
    q = expand(p)
    return q if q.is_absolute() else DEVICE_DIR / q


@dataclass
class _Fall:
    raw: dict[str, Any]
    t0: datetime
    clip: Future  # Future[Path | None]: set by the fall's own worker, shared with a hat_knocked_off
    knocked: bool = False


@dataclass
class _Off:
    arrival: datetime
    due: float


@dataclass
class Bridge:
    cfg: ArgusConfig
    launch: bool | None = None
    tof_log: Path | None = None
    from_start: bool = False
    policy: ClipPolicy | None = None
    poll_s: float = 0.5
    wall_fn: Callable[[], datetime] = lambda: datetime.now(UTC)
    mono_fn: Callable[[], float] = time.monotonic
    queue: EventQueue = field(init=False)

    def __post_init__(self) -> None:
        b = self.cfg.bridge
        argus_dir = expand(b.argus_dir)
        self.events_path = expand(b.events_path) if b.events_path else argus_dir / "events.jsonl"
        self.clips_dir = expand(b.clips_dir) if b.clips_dir else argus_dir / "clips"
        self.air_log = expand(b.air_log)
        self.state_dir = resolve_state_dir(b.state_dir)
        self.staging = self.state_dir / "staging"
        self.staging.mkdir(parents=True, exist_ok=True)
        self.policy = self.policy or ClipPolicy(wait_s=b.clip_wait_s)
        self.queue = EventQueue(self.cfg.events.queue_dir, self.cfg.events.queue_max_mb)
        self.state = StateFile(self.state_dir / "bridge_state.json")
        self.tailer = EventsTailer(self.events_path, self.state, from_start=self.from_start)
        self.do_launch = b.launch if self.launch is None else self.launch
        self._pool = ThreadPoolExecutor(8, thread_name_prefix="bridge-event")
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._inflight: set[str] = set()
        self._falls: dict[str, _Fall] = {}
        self._near_misses: deque[tuple[str, datetime]] = deque(maxlen=50)
        self._offs: list[_Off] = []
        self._hat_on = True
        self.tof_lines = 0
        self._tof_warned = False
        self._started = self.mono_fn()
        self._tof_log_pos: int | None = None
        self.launcher: HatLauncher | None = None
        self.queued: list[str] = []  # event ids enqueued (tests / logs)
        self.futures: list[Future] = []

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self.do_launch:
            b = self.cfg.bridge
            cmd = [str(expand(b.python)), "-m", "hat.main", *b.hat_args]
            self.launcher = HatLauncher(cmd, expand(b.argus_dir), self.on_tof)
            self.launcher.start()
        for raw in list(self.state.pending.values()):  # events that were mid-wait when we last stopped
            log.info("resuming pending event %s", raw.get("event_id"))
            self._dispatch(raw)

    def stop(self) -> None:
        self._stop.set()
        if self.launcher:
            self.launcher.stop()
        self._pool.shutdown(wait=True)
        self.state.save()

    def run(self, stop: threading.Event) -> None:
        self.start()
        try:
            while not stop.wait(self.poll_s):
                self.step()
        finally:
            self.stop()

    def step(self) -> None:
        """One loop pass: new events, the optional tof log, then any HELMET OFF that is due."""
        for raw in self.tailer.poll():
            self._accept(raw)
        self._poll_tof_log()
        self._decide_offs()
        self._warn_if_no_tof()

    # ------------------------------------------------------------ their events
    def _accept(self, raw: dict[str, Any]) -> None:
        if not mapper.is_hat_event(raw):
            log.debug("ignoring %r event", raw.get("type"))
            return
        eid = raw["event_id"]
        with self._lock:
            if eid in self._inflight or eid in self.state.seen:
                log.info("duplicate event %s ignored", eid)
                return
            self.state.pending[eid] = raw
            self.state.save()
        self._dispatch(raw)

    def _dispatch(self, raw: dict[str, Any]) -> None:
        eid = raw["event_id"]
        with self._lock:
            if eid in self._inflight:
                return
            self._inflight.add(eid)
            t0 = mapper.parse_ts(raw["timestamp"])
            fut: Future = Future()
            nms = list(self._near_misses)
            if raw["type"] == "fall":
                self._falls[eid] = _Fall(raw, t0, fut)
            else:
                self._near_misses.append((eid, t0))
            hat_on = self._hat_on
            self._prune(t0)
        self.futures.append(self._pool.submit(self._handle, raw, fut, nms, hat_on))

    def _prune(self, now: datetime) -> None:
        for k in [k for k, f in self._falls.items() if (now - f.t0).total_seconds() > KEEP_S]:
            del self._falls[k]

    def _handle(self, raw: dict[str, Any], clip_fut: Future, near_misses: list, hat_on: bool) -> None:
        eid = raw["event_id"]
        try:
            clip = wait_for_clip(eid, self.clips_dir, self.staging, self.policy, stop=self._stop.is_set)
            if clip is None and self._stop.is_set():
                log.info("stopping: %s stays pending and is retried on the next start", eid)
                clip_fut.set_result(None)
                return
            clip_fut.set_result(clip)
            t0 = mapper.parse_ts(raw["timestamp"])
            m = mapper.map_hat_event(raw, serial=self._serial(), side_map=self.cfg.bridge.side_sensor,
                                     air=latest_air(self.air_log, t0, self.cfg.bridge.air_max_age_s), hat_on=hat_on,
                                     recent_near_misses=near_misses)
            for n in m.notes:
                log.warning("%s (mapping): %s: %s", ISSUE, eid, n)
            if clip is None:
                log.warning("%s (timing): no clip for %s within %.0fs; enqueueing without video", ISSUE, eid,
                            self.policy.wait_s)
            self._enqueue(m, clip)
            self._done(eid)
        except Exception:
            if not clip_fut.done():
                clip_fut.set_result(None)
            log.exception("event %s failed; it stays pending and is retried on the next start", eid)

    def _serial(self) -> str:
        return (self.cfg.device.serial or "").strip() or "unprovisioned"

    def _done(self, eid: str) -> None:
        with self._lock:
            self.state.pending.pop(eid, None)
            if eid not in self.state.seen:
                self.state.seen.append(eid)
            self._inflight.discard(eid)
            self.state.save()

    def _enqueue(self, m: mapper.Mapped, clip: Path | None, clip_offset_s: float = 0.0) -> None:
        ev = m.event
        eid = ev["event_id"]
        if (self.queue.root / eid).exists():
            log.info("event %s already in the queue", eid)
            return
        tmp = self.queue.tmp_dir(eid)
        clip_s: float | None = None
        if clip is not None:
            shutil.copyfile(clip, tmp / "clip.mp4")
            clip_s = probe_duration_s(tmp / "clip.mp4")
            if clip_s is None:
                clip_s = DEFAULT_CLIP_S
                log.warning("no ffprobe: assuming a %.0fs clip for %s", clip_s, eid)
        if m.wants_clip:
            mapper.with_clip(ev, clip_s, offset_s=clip_offset_s)
        if m.trace is not None:
            try:
                validate_trace(m.trace)
            except SchemaError as e:
                log.error("%s (contract): trace for %s rejected, event goes up without it: %s", ISSUE, eid, e)
            else:
                (tmp / "trace.json").write_text(json.dumps(m.trace, separators=(",", ":")))
        try:
            self.queue.commit(ev, tmp, clip_s)
        except SchemaError as e:
            log.error("%s (contract): event %s rejected by event.schema.json, dropped: %s", ISSUE, eid, e)
            return
        self.queued.append(eid)
        log.info("event %s (%s) queued%s", eid, ev["type"], "" if clip else " without video")

    # ------------------------------------------------------------ hat on / off
    def on_tof(self, t: TofLine) -> None:
        self.tof_lines += 1
        with self._lock:
            self._hat_on = t.on
            if not t.on:
                now = self.wall_fn()
                self._offs.append(_Off(now, self.mono_fn() + TOF_SETTLE_S))
                log.info("HELMET OFF seen")

    def _poll_tof_log(self) -> None:
        """--tof-log: a file their stdout is piped to. Starts at the end, like the events file."""
        if self.tof_log is None:
            return
        try:
            size = self.tof_log.stat().st_size
        except OSError:
            return
        if self._tof_log_pos is None or size < self._tof_log_pos:
            self._tof_log_pos = size if self._tof_log_pos is None else 0
        if size == self._tof_log_pos:
            return
        with self.tof_log.open("rb") as f:
            f.seek(self._tof_log_pos)
            data = f.read()
        cut = data.rfind(b"\n")
        if cut < 0:
            return
        self._tof_log_pos += cut + 1
        for line in data[: cut + 1].decode("utf-8", "replace").splitlines():
            t = parse_tof(line)
            if t:
                self.on_tof(t)

    def _warn_if_no_tof(self) -> None:
        if (not self._tof_warned and self.tof_lines == 0 and (self.do_launch or self.tof_log)
                and self.mono_fn() - self._started > 120):
            self._tof_warned = True
            log.warning("%s (hardware-truth): no '[tof] HELMET' line in 120 s; hat_removed / hat_knocked_off cannot "
                        "fire. Did their console format change, or is the ToF down?", ISSUE)

    def _decide_offs(self) -> None:
        now = self.mono_fn()
        with self._lock:
            due, self._offs = [o for o in self._offs if o.due <= now], [o for o in self._offs if o.due > now]
        for off in due:
            self._decide_off(off)

    def _decide_off(self, off: _Off) -> None:
        b = self.cfg.bridge
        # Their ToF prints OFF only after the hat has been off for tof_off_dwell_s, so the hat left that much earlier.
        off_start = off.arrival - timedelta(seconds=b.tof_off_dwell_s)
        fall: _Fall | None = None
        with self._lock:
            for f in sorted(self._falls.values(), key=lambda f: f.t0, reverse=True):
                if not f.knocked and -0.1 <= (off_start - f.t0).total_seconds() <= b.knock_window_s:
                    fall = f
                    f.knocked = True
                    break
        eid = str(uuid.uuid4())
        air = latest_air(self.air_log, off_start, b.air_max_age_s)
        m = mapper.map_hat_off(event_id=eid, off_start=off_start, detected=off.arrival, serial=self._serial(),
                               fall=fall.raw if fall else None, fall_t0=fall.t0 if fall else None, air=air)
        log.info("HELMET OFF -> %s", m.event["type"])
        self.futures.append(self._pool.submit(self._finish_off, m, fall, off_start))

    def _finish_off(self, m: mapper.Mapped, fall: _Fall | None, off_start: datetime) -> None:
        try:
            clip = None
            if fall is not None:  # share the fall's clip: wait for the fall's own worker, then copy the same file
                clip = fall.clip.result(timeout=self.policy.wait_s + 120)
            self._enqueue(m, clip, clip_offset_s=(off_start - fall.t0).total_seconds() if fall else 0.0)
        except Exception:
            log.exception("%s event failed", m.event["type"])
