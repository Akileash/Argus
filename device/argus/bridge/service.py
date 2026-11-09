"""The bridge: tail their files, wait for clips, map, and write into OUR queue (the uploader sends from there)."""

from __future__ import annotations

import dataclasses
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
from argus.bridge.launcher import HatLauncher, TofLine, parse_tof_all
from argus.bridge.tail import EventsTailer, StateFile
from argus.config import ArgusConfig
from argus.events.queue import DEVICE_DIR, EventQueue, SchemaError, validate_trace

log = logging.getLogger("argus.bridge")
ISSUE = "INTEGRATION ISSUE"  # grep-able marker in the journal for things the PR should raise
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
    probe_fn: Callable[[Path], float | None] = probe_duration_s
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
        self._hat_on: bool | None = None  # last parsed `[tof] HELMET` state; None until one is seen
        self._last_lof: datetime | None = None  # t0 of the last line_of_fire let through
        self.skipped: dict[str, int] = {}
        self.air_launcher: HatLauncher | None = None
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
            if b.air_launch:
                sensors = expand(b.argus_dir) / "pi_sensors"
                py = expand(b.air_python) if b.air_python else sensors / ".venv" / "bin" / "python"
                self.air_launcher = HatLauncher([str(py), "-m", "argus_sensors", *b.air_args], sensors,
                                                name="argus_sensors(scd41)")
                self.air_launcher.start()
        for raw in list(self.state.pending.values()):  # events that were mid-wait when we last stopped
            if self._stale(raw):
                self._mark_seen(raw["event_id"])
                continue
            log.info("resuming pending event %s", raw.get("event_id"))
            self._dispatch(raw)

    def stop(self) -> None:
        self._stop.set()
        for ln in (self.launcher, self.air_launcher):
            if ln:
                ln.stop()
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
            typ = str(raw.get("type"))
            if typ in mapper.LEGACY_TYPES:
                self._skip(f"legacy:{typ}", f"skipped legacy imu_fall.py {typ!r} line")
            else:
                log.debug("ignoring %r line", typ)
            return
        eid = raw["event_id"]
        with self._lock:
            if eid not in self._inflight and eid not in self.state.seen and self._stale(raw):
                self._mark_seen(eid)
                return
            if raw["type"] == "line_of_fire" and eid not in self._inflight and eid not in self.state.seen:
                t0 = mapper.parse_ts(raw["timestamp"])
                last = self._last_lof
                if last is not None and 0 <= (t0 - last).total_seconds() < self.cfg.bridge.lof_min_interval_s:
                    self._skip("lof_throttled", f"line_of_fire {eid} within {self.cfg.bridge.lof_min_interval_s:.0f}s "
                               "of the last one: not uploaded (their camera skips a capture while one runs)")
                    self._near_misses.append((eid, t0))  # not uploaded, but a fall right after still links to it
                    self._mark_seen(eid)
                    return
                self._last_lof = t0
            if eid in self._inflight or eid in self.state.seen:
                log.info("duplicate event %s ignored", eid)
                return
            self.state.pending[eid] = raw
            self.state.save()
        self._dispatch(raw)

    def _stale(self, raw: dict[str, Any]) -> bool:
        """True (and logged + counted) if this event is older than bridge.max_event_age_s: never queue an old session's event."""
        limit = self.cfg.bridge.max_event_age_s
        if limit <= 0:
            return False
        age = (self.wall_fn() - mapper.parse_ts(raw["timestamp"])).total_seconds()
        if age <= limit:
            return False
        self._skip("stale", f"{raw.get('type')} {raw.get('event_id')} is {age:.0f}s old (limit {limit:.0f}s): discarded, "
                   "not uploaded")
        return True

    def _skip(self, key: str, msg: str) -> None:
        self.skipped[key] = self.skipped.get(key, 0) + 1
        log.info("%s (total %d)", msg, self.skipped[key])

    def _mark_seen(self, eid: str) -> None:
        with self._lock:
            self.state.pending.pop(eid, None)
            if eid not in self.state.seen:
                self.state.seen.append(eid)
            self.state.save()

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
            last_tof = self._hat_on
            self._prune(t0)
        self.futures.append(self._pool.submit(self._handle, raw, fut, nms, last_tof))

    def _prune(self, now: datetime) -> None:
        for k in [k for k, f in self._falls.items() if (now - f.t0).total_seconds() > KEEP_S]:
            del self._falls[k]

    def _resolve_hat_on(self, raw: dict[str, Any], last_tof: bool | None) -> bool | None:
        """fall: their helmet_worn_at_onset; line_of_fire: the last `[tof] HELMET` state. Unknown -> bridge.hat_on_unknown."""
        if raw["type"] == "fall":
            w = mapper.reported_worn(raw)
            if w is not None:
                return w
        if last_tof is not None:
            return last_tof
        return {"skip": None, "on": True, "off": False}[self.cfg.bridge.hat_on_unknown]

    def _handle(self, raw: dict[str, Any], clip_fut: Future, near_misses: list, last_tof: bool | None) -> None:
        eid = raw["event_id"]
        try:
            hat_on = self._resolve_hat_on(raw, last_tof)
            if hat_on is None:
                log.error("%s (contract): %s %s dropped: hat state unknown (no `[tof] HELMET` line seen yet) and "
                          "metrics.hat_on is a required boolean", ISSUE, raw["type"], eid)
                self._skip("hat_on_unknown", f"{raw['type']} {eid} not uploaded: hat state unknown")
                clip_fut.set_result(None)
                self._done(eid)
                return
            policy = self.policy
            if raw["type"] == "line_of_fire":
                policy = dataclasses.replace(policy, wait_s=self.cfg.bridge.lof_clip_wait_s)
            clip = wait_for_clip(eid, self.clips_dir, self.staging, policy, stop=self._stop.is_set)
            if clip is None and self._stop.is_set():
                log.info("stopping: %s stays pending and is retried on the next start", eid)
                clip_fut.set_result(None)
                return
            clip_fut.set_result(clip)
            if self._stale(raw):  # the clip wait can outlast the limit
                self._done(eid)
                return
            t0 = mapper.parse_ts(raw["timestamp"])
            m = mapper.map_hat_event(raw, serial=self._serial(), side_map=self.cfg.bridge.side_sensor,
                                     air=latest_air(self.air_log, t0, self.cfg.bridge.air_max_age_s), hat_on=hat_on,
                                     recent_near_misses=near_misses)
            for n in m.notes:
                log.warning("%s (mapping): %s: %s", ISSUE, eid, n)
            if clip is None:
                log.warning("%s (timing): no clip for %s within %.0fs; enqueueing without video", ISSUE, eid,
                            self.policy.wait_s)
            self._enqueue(m, clip, t0, self._clip_end(eid, clip))
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

    def _clip_end(self, eid: str, clip: Path | None) -> datetime | None:
        """When the clip was finished: mtime of the file their camera wrote (the .h264 if we remuxed a copy)."""
        if clip is None:
            return None
        src = self.clips_dir / f"{eid}.h264" if clip.parent == self.staging else clip
        try:
            return datetime.fromtimestamp(src.stat().st_mtime, UTC)
        except OSError:
            return None

    def _enqueue(self, m: mapper.Mapped, clip: Path | None, t0: datetime | None = None,
                 clip_end: datetime | None = None) -> None:
        ev = m.event
        eid = ev["event_id"]
        if (self.queue.root / eid).exists():
            log.info("event %s already in the queue", eid)
            return
        tmp = self.queue.tmp_dir(eid)
        clip_s: float | None = None
        if clip is not None:
            shutil.copyfile(clip, tmp / "clip.mp4")
            clip_s = self.probe_fn(tmp / "clip.mp4")
            if clip_s is None:
                log.warning("%s (timing): cannot read the length of the clip for %s (ffprobe missing or file "
                            "unreadable); window left at 0/0", ISSUE, eid)
        if m.wants_clip and t0 is not None:
            mapper.with_clip(ev, t0, clip_end, clip_s)
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
            for t in parse_tof_all(line):
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
            clip, end = None, None
            if fall is not None:  # share the fall's clip: wait for the fall's own worker, then copy the same file
                clip = fall.clip.result(timeout=self.policy.wait_s + 120)
                end = self._clip_end(fall.raw["event_id"], clip)
            self._enqueue(m, clip, off_start, end)
        except Exception:
            log.exception("%s event failed", m.event["type"])
