"""L3 event manager (spec §3.4, §4.4): debounce/merge, cooldown, near-miss -> contact upgrade, clip + trace cut.

Rules
* Event types: struck_by_near_miss, struck_by_contact, head_impact, fall, hat_knocked_off (clip + trace),
  hat_removed and module_fallback (event.json only). struck_by_warning and hat_on are NOT events (they land in
  the trace's ``detections``).
* A trigger during an open event's post-roll that is *mergeable* extends it (post-roll ends at the trigger plus
  ``post_roll_s``, capped at ``merge_extend_max_s`` beyond the original end) and is recorded in
  ``merged_triggers``. Mergeable = same type, or a head_impact inside an open struck_by_* event (the same blow).
  A near miss followed by contact upgrades the open event's type instead of making a second one.
  Other type combinations (e.g. head_impact then hat_knocked_off) are separate events.
* After an event closes the same type is ignored for ``cooldown_s``.
* Finalising (``tick``): camera.save_clip(t0 - pre, end), trace.json for the same window, event.json, queue commit.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from argus.bus import SampleBus
from argus.config import ArgusConfig
from argus.detectors.base import Detection
from argus.detectors.classify import device_class
from argus.events.queue import EventQueue, SchemaError, validate_trace
from argus.events.trace import TraceRecorder, build_trace

log = logging.getLogger(__name__)

CLIP_TYPES = frozenset({"struck_by_near_miss", "struck_by_contact", "head_impact", "fall", "hat_knocked_off"})
LOG_ONLY_TYPES = frozenset({"hat_removed", "module_fallback"})
EVENT_TYPES = CLIP_TYPES | LOG_ONLY_TYPES
STRUCK_BY = frozenset({"struck_by_near_miss", "struck_by_contact"})
METRIC_KEYS = ("peak_g", "peak_g_saturated", "closing_rate_mps", "ttc_s", "range_m", "free_fall_ms", "drop_height_m")
UNPROVISIONED = "unprovisioned"
HISTORY_MARGIN_S = 1.5
NS = 1_000_000_000


@dataclass
class _Open:
    event_id: str
    type: str
    t0_ns: int
    detected_ns: int
    end_ns: int
    max_end_ns: int
    sensor_id: str | None
    metrics: dict[str, Any]
    hat_on: bool
    merged: list[dict[str, Any]] = field(default_factory=list)


def mergeable(open_type: str, new_type: str) -> bool:
    return new_type == open_type or (open_type in STRUCK_BY and new_type in STRUCK_BY | {"head_impact"})


class EventManager:
    def __init__(
        self,
        cfg: ArgusConfig,
        bus: SampleBus,
        camera: Any,
        queue: EventQueue,
        recorder: TraceRecorder,
        *,
        now_fn: Callable[[], int],
        hat_on_fn: Callable[[], bool],
        impls_fn: Callable[[], tuple[dict[str, str], list[str]]] = lambda: ({}, []),
        wall_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
        async_cut: bool = False,
    ) -> None:
        self.cfg, self.bus, self.camera, self.queue, self.rec = cfg, bus, camera, queue, recorder
        self.ev = cfg.events
        self.now_fn, self.hat_on_fn, self.impls_fn, self.wall_fn = now_fn, hat_on_fn, impls_fn, wall_fn
        self.saturation_g = float(cfg.thresholds.fall_impact.saturation_g)
        self._open: list[_Open] = []
        self._cool: dict[str, int] = {}  # type -> ignore until (ns)
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(1, thread_name_prefix="argus-cut") if async_cut else None
        self._futures: list[Future] = []
        self.written: list[str] = []  # event ids committed (tests / logs)
        self.dropped: list[tuple[str, str]] = []  # (type, reason)

    # ------------------------------------------------------------ input
    def on_detection(self, det: Detection, now_ns: int | None = None) -> str | None:
        """Returns the event id the detection opened or merged into, else None."""
        if det.type not in EVENT_TYPES:
            return None
        now = self.now_fn() if now_ns is None else now_ns
        with self._lock:
            t0 = det.t_ns
            if det.type in ("hat_knocked_off", "hat_removed"):  # stamp when the hat left the head
                t0 = int(det.debug.get("off_start_ns", t0))
            for ev in self._open:
                if t0 >= ev.t0_ns and mergeable(ev.type, det.type):
                    return self._merge(ev, det, t0)
            if t0 < self._cool.get(det.type, 0):
                self.dropped.append((det.type, "cooldown"))
                log.info("event %s ignored: cooldown", det.type)
                return None
            return self._open_event(det, t0, now)

    def on_fallback(self, rec: dict[str, Any]) -> str | None:
        """Supervisor ``module_fallback`` record -> an event.json-only system event."""
        now = self.now_fn()
        t = int(rec.get("t_ns", now))
        eid = str(uuid.uuid4())
        ev = _Open(eid, "module_fallback", t, now, t, t, None, {}, self.hat_on_fn())
        log.warning("module_fallback event: %s", rec)
        self._submit(ev, extra_fallback=rec.get("module"))
        return eid

    # ------------------------------------------------------------ open / merge
    def _metrics(self, det: Detection) -> dict[str, Any]:
        return {k: getattr(det, k) for k in METRIC_KEYS if getattr(det, k) is not None and getattr(det, k) is not False}

    def _open_event(self, det: Detection, t0: int, now: int) -> str:
        post = 0.0 if det.type in LOG_ONLY_TYPES else self.ev.post_roll_s
        end = t0 + int(post * NS)
        ev = _Open(str(uuid.uuid4()), det.type, t0, now, end, end + int(self.ev.merge_extend_max_s * NS),
                   det.sensor_id, self._metrics(det), self.hat_on_fn())
        if det.type in LOG_ONLY_TYPES:
            self._cool[det.type] = t0 + int(self.ev.cooldown_s * NS)
            self._submit(ev)
        else:
            self._open.append(ev)
        return ev.event_id

    def _merge(self, ev: _Open, det: Detection, t0: int) -> str:
        if ev.type == "struck_by_near_miss" and det.type == "struck_by_contact":
            ev.type = "struck_by_contact"  # upgrade in place (spec §4.1)
            ev.metrics.update(self._metrics(det))
            ev.sensor_id = det.sensor_id or ev.sensor_id
        elif det.type != ev.type:
            for k, v in self._metrics(det).items():
                ev.metrics.setdefault(k, v)  # e.g. the contact's peak_g from the head_impact detection
            ev.merged.append({"type": det.type, "offset_s": round((t0 - ev.t0_ns) / NS, 3)})
        else:
            ev.merged.append({"type": det.type, "offset_s": round((t0 - ev.t0_ns) / NS, 3)})
        ev.end_ns = min(max(ev.end_ns, t0 + int(self.ev.post_roll_s * NS)), ev.max_end_ns)
        return ev.event_id

    # ------------------------------------------------------------ finalise
    def tick(self, now_ns: int | None = None) -> None:
        now = self.now_fn() if now_ns is None else now_ns
        with self._lock:
            due = [e for e in self._open if e.end_ns <= now]
            for e in due:
                self._open.remove(e)
                self._submit(e)

    def flush(self, now_ns: int | None = None) -> None:
        """Close every open event now (shutdown): the post-roll is truncated to what has been seen."""
        now = self.now_fn() if now_ns is None else now_ns
        with self._lock:
            pending, self._open = self._open, []
            for e in pending:
                e.end_ns = min(e.end_ns, now)
                self._submit(e)
        self.drain()

    def drain(self) -> None:
        for f in list(self._futures):
            f.result()
        self._futures.clear()

    def open_events(self) -> list[_Open]:
        with self._lock:
            return list(self._open)

    def _submit(self, ev: _Open, extra_fallback: str | None = None) -> None:
        self._cool[ev.type] = max(self._cool.get(ev.type, 0), ev.end_ns + int(self.ev.cooldown_s * NS))
        if ev.type == "struck_by_contact":
            self._cool["struck_by_near_miss"] = max(self._cool.get("struck_by_near_miss", 0),
                                                    ev.end_ns + int(self.ev.cooldown_s * NS))
        if self._pool is None:
            self._write_safe(ev, extra_fallback)
        else:
            self._futures.append(self._pool.submit(self._write_safe, ev, extra_fallback))

    def _write_safe(self, ev: _Open, extra_fallback: str | None) -> None:
        try:
            self._write(ev, extra_fallback)
        except Exception:
            log.exception("failed to write event %s (%s)", ev.event_id, ev.type)

    def _utc(self, t_ns: int, mono: int, wall: datetime) -> str:
        """utc = wall_at_write - (mono_at_write - t_ns); once, in the writer (device_queue.md)."""
        dt = wall - timedelta(microseconds=(mono - t_ns) / 1000)
        return dt.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def _write(self, ev: _Open, extra_fallback: str | None = None) -> None:
        mono, wall = self.now_fn(), self.wall_fn()
        tmp = self.queue.tmp_dir(ev.event_id)
        clip_s: float | None = None
        pre_s = post_s = 0.0
        if ev.type in CLIP_TYPES:
            end_ns = max(ev.end_ns, ev.t0_ns)
            pre_ns = int(self.ev.pre_roll_s * NS)
            floor = min(end_ns, mono) - int((self.bus.history_s - HISTORY_MARGIN_S) * NS)
            start_ns = max(ev.t0_ns - pre_ns, floor)  # the bus (and camera ring) are finite
            pre_s, post_s = (ev.t0_ns - start_ns) / NS, (end_ns - ev.t0_ns) / NS
            try:
                info = self.camera.save_clip(start_ns, end_ns, str(tmp / "clip.mp4"))
                clip_s = info.duration_s
            except Exception as e:  # noqa: BLE001 - an event without video still matters
                log.error("save_clip failed for %s: %s", ev.event_id, e)
                (tmp / "clip.mp4").unlink(missing_ok=True)
            trace = build_trace(ev.event_id, ev.t0_ns, start_ns, end_ns, self.bus, self.rec, self.saturation_g)
            try:
                validate_trace(trace)
            except SchemaError:
                log.exception("trace for %s invalid; dropping the trace, keeping the event", ev.event_id)
            else:
                (tmp / "trace.json").write_text(json.dumps(trace, separators=(",", ":")))
        impls, fallbacks = self.impls_fn()
        if extra_fallback and extra_fallback not in fallbacks:
            fallbacks = [*fallbacks, extra_fallback]
        env = self.bus.latest("env")
        metrics = {k: ev.metrics.get(k) for k in METRIC_KEYS}
        metrics["peak_g_saturated"] = bool(ev.metrics.get("peak_g_saturated", False))
        metrics["sensor_id"] = ev.sensor_id
        metrics["hat_on"] = ev.hat_on
        doc: dict[str, Any] = {
            "schema_version": "1",
            "event_id": ev.event_id,
            "device_serial": self.cfg.device.serial or UNPROVISIONED,
            "type": ev.type,
            "device_class": device_class(ev.type),
            "t0": self._utc(ev.t0_ns, mono, wall),
            "detected_at": self._utc(ev.detected_ns, mono, wall),
            "window": {"pre_s": round(pre_s, 3), "post_s": round(post_s, 3)},
            "metrics": metrics,
            "env": None if env is None else {"co2_ppm": env.co2_ppm, "temp_c": env.temp_c, "rh_pct": env.rh_pct},
            "merged_triggers": ev.merged,
            "media": [],
            "firmware": {"version": self.ev.firmware_version, "impls": impls, "fallbacks_active": fallbacks},
        }
        self.queue.commit(doc, tmp, clip_s)
        self.written.append(ev.event_id)
        log.info("event %s queued (%s)", ev.event_id, ev.type)
