"""L2 + L3 wiring: bus -> detectors -> haptics + event manager -> on-disk queue (spec §5.1).

``wire(sup, bus, cfg)`` is called by ``argus-cli run`` / ``replay`` before the supervisor starts. It builds a
:class:`Pipeline`, starts its runtime thread and returns the stop function. For deterministic tests build a
``Pipeline`` directly (``start_thread=False``) and call :meth:`Pipeline.step` on a virtual clock.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, ClassVar

from argus.bus import SampleBus
from argus.config import ArgusConfig
from argus.detectors.base import Detection, DetectorState
from argus.detectors.fall_impact import FallImpactDetector
from argus.detectors.hat_state import HatStateDetector
from argus.detectors.struck_by import StruckByDetector
from argus.events.haptics import HapticPlayer
from argus.events.manager import EventManager
from argus.events.queue import EventQueue
from argus.events.trace import TraceRecorder
from argus.status_led import StatusLed, open_led
from argus.supervisor import Supervisor

log = logging.getLogger(__name__)

STREAM_PRIORITY = {"imu": 0, "tof": 1, "range": 2, "env": 3}
LOOP_S = 0.005
HOLDBACK_NS = 20_000_000  # live thread: wait 20 ms so a slower stream's samples can be merged in order


class Pipeline:
    def __init__(
        self,
        sup: Supervisor,
        bus: SampleBus,
        cfg: ArgusConfig,
        *,
        queue_dir: str | None = None,
        wall_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
        async_cut: bool = True,
        led: StatusLed | None = None,
    ) -> None:
        self.sup, self.bus, self.cfg = sup, bus, cfg
        self.led = led
        self.boot_phase = ""
        self.clock = sup.clock
        self.state = DetectorState(hat_on=True)
        self.struck_by = StruckByDetector(cfg.detector_cfg("struck_by"))
        self.fall = FallImpactDetector(cfg.detector_cfg("fall_impact"))
        self.hat = HatStateDetector(cfg.detector_cfg("hat_state"), impact_source=self.fall)
        self.detectors = (self.struck_by, self.fall, self.hat)
        self.recorder = TraceRecorder()
        self.queue = EventQueue(queue_dir or cfg.events.queue_dir, cfg.events.queue_max_mb)
        self.player = HapticPlayer(sup.haptics, self.struck_by.haptic_request, lambda: self.state.hat_on,
                                   self.recorder)
        self.manager = EventManager(cfg, bus, sup.camera, self.queue, self.recorder, now_fn=self.clock.now_ns,
                                    hat_on_fn=lambda: self.state.hat_on, impls_fn=self._impls, wall_fn=wall_fn,
                                    async_cut=async_cut)
        self.detections: list[Detection] = []  # everything detected (tests, debugging); bounded
        self._inbox: deque[tuple[int, int, int, str, Any]] = deque()
        self._pending: list[tuple[int, int, int, str, Any]] = []
        self._seq = 0
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._unsubs = [bus.subscribe(s, self._make_cb(s)) for s in STREAM_PRIORITY]
        # supervisor module_fallback records become system events
        prev = sup.on_event

        def on_event(rec: dict[str, Any]) -> None:
            prev(rec)
            if rec.get("type") == "module_fallback":
                self.manager.on_fallback(rec)

        sup.on_event = on_event
        sup.pipeline = self  # type: ignore[attr-defined]
        handlers = getattr(sup, "control_handlers", None)
        if handlers is None:
            handlers = sup.control_handlers = {}  # type: ignore[attr-defined]
        handlers["trigger"] = self._control_trigger
        self._update_phase()

    # ------------------------------------------------------------ plumbing
    def _impls(self) -> tuple[dict[str, str], list[str]]:
        st = self.sup.status()
        impls = {n: v["impl"] for n, v in st.items() if v["impl"]}
        return impls, [n for n, v in st.items() if v["on_fallback"]]

    def _make_cb(self, stream: str) -> Callable[[Any], None]:
        prio = STREAM_PRIORITY[stream]

        def cb(s: Any) -> None:
            self._seq += 1
            self._inbox.append((s.t_ns, prio, self._seq, stream, s))

        return cb

    # ------------------------------------------------------------ boot prompts (spec §4.3)
    PHASE_TEXT: ClassVar[dict[str, str]] = {
        "calibrating": "CALIBRATING: keep the hat still, resting on a table",
        "put_on": "PUT THE HAT ON your head now (calibrating the hat-on sensor, hold still for ~3 s)",
        "ready": "READY: calibration done",
    }
    PHASE_LED: ClassVar[dict[str, str]] = {"calibrating": "solid", "put_on": "blink", "ready": "off"}

    def _update_phase(self) -> None:
        if self.struck_by.imu.calibrated:
            phase = "ready" if self.hat.calibrated else "put_on"
        else:
            phase = "calibrating"
        if phase == self.boot_phase:
            return
        self.boot_phase = phase
        log.info("[boot] %s", self.PHASE_TEXT[phase])
        if self.led is not None:
            self.led.set_mode(self.PHASE_LED[phase])

    # ------------------------------------------------------------ the runtime step
    def step(self, now_ns: int | None = None, holdback_ns: int = 0) -> list[Detection]:
        """Process every new sample older than ``now - holdback`` in timestamp order. Returns new detections."""
        now = self.clock.now_ns() if now_ns is None else now_ns
        out: list[Detection] = []
        with self._lock:
            while self._inbox:
                self._pending.append(self._inbox.popleft())
            self._pending.sort(key=lambda x: x[:3])
            cut = now - holdback_ns
            n = 0
            for item in self._pending:
                if item[0] > cut:
                    break
                n += 1
            ready, self._pending = self._pending[:n], self._pending[n:]
            for _, _, _, stream, s in ready:
                out.extend(self._feed(stream, s, now))
            self.player.tick(now)
            self.manager.tick(now)
            self._update_phase()
        return out

    def _feed(self, stream: str, s: Any, now: int) -> list[Detection]:
        dets: list[Detection] = []
        method = f"on_{stream}"
        for d in self.detectors:
            dets.extend(getattr(d, method)(s, self.state))
        if stream == "range":
            self._harvest_closing(s)
        if stream == "tof" and self.hat.hat_on != self.state.hat_on:
            self.state.hat_on = self.hat.hat_on
            self.recorder.record_hat(s.t_ns, self.state.hat_on)
        for d in dets:
            self._dispatch(d, now)
        return dets

    def _harvest_closing(self, s: Any) -> None:
        log_ = self.struck_by.candidate_log
        if log_ and log_[-1]["t_ns"] == s.t_ns:
            e = log_[-1]
            self.recorder.record_closing(s.sensor_id, s.t_ns, e["v_obj_mps"])

    def _dispatch(self, d: Detection, now: int) -> None:
        self.detections.append(d)
        if len(self.detections) > 5000:
            del self.detections[:1000]
        self.recorder.record_detection(d)
        self.player.on_detection(d, now)
        self.manager.on_detection(d, now)

    def inject(self, det: Detection, now_ns: int | None = None) -> str | None:
        """Push a synthetic detection through haptics + manager (``argus-cli trigger``). Returns the event id."""
        now = self.clock.now_ns() if now_ns is None else now_ns
        with self._lock:
            self.detections.append(det)
            self.recorder.record_detection(det)
            self.player.on_detection(det, now)
            return self.manager.on_detection(det, now)

    def _control_trigger(self, sup: Supervisor, req: dict[str, Any]) -> dict[str, Any]:
        from argus.events.synthetic import synthesize

        try:
            det = synthesize(req["type"], self.clock.now_ns())
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        eid = self.inject(det)
        if eid is None:
            return {"ok": False, "error": "trigger ignored (cooldown)"}
        return {"ok": True, "event_id": eid, "post_roll_s": self.cfg.events.post_roll_s}

    # ------------------------------------------------------------ thread
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="argus-pipeline", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.step(holdback_ns=HOLDBACK_NS)
            except Exception:
                log.exception("pipeline step failed")
            self._stop.wait(LOOP_S)

    def stop(self, flush: bool = True) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        for u in self._unsubs:
            u()
        self._unsubs = []
        try:
            self.step(holdback_ns=0)
            self.player.off_all(self.clock.now_ns())
            if flush:
                self.manager.flush()
            else:
                self.manager.drain()
        except Exception:
            log.exception("pipeline shutdown failed")
        if self.led is not None:
            self.led.close()


def wire(sup: Supervisor, bus: SampleBus, cfg: ArgusConfig) -> Callable[[], None]:
    """Pipeline hook for ``argus-cli`` (see cli._wire_pipeline). Never depends on backend pairing."""
    p = Pipeline(sup, bus, cfg, led=open_led(cfg.device.status_led_pin))
    p.start()
    return p.stop
