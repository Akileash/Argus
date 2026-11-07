"""L3 haptic pattern player (spec §4.1, §4.2). The drivers only know ``set(motor, intensity)``.

* struck_by warning: directional pulses from ``StruckByDetector.haptic_request`` (back motor for R1-R4,
  top for TOP; 4 Hz at TTC 1.3 s rising to continuous below 0.5 s). Pulses are 50 % duty.
* fall / head impact / struck_by contact: both motors, one 1.5 s buzz (acknowledges the logged event).
* hat_removed / hat_knocked_off / hat_on: no haptic. Nothing plays while the hat is off.

``on_detection`` acts immediately (target < 50 ms trigger -> motor); ``tick`` advances pulse edges and
should be called every few ms. Time comes from the caller, so it runs on a virtual clock.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from argus.detectors.base import Detection
from argus.events.trace import TraceRecorder

ACK_TYPES = frozenset({"fall", "head_impact", "struck_by_contact"})
ACK_S = 1.5
MOTORS = ("top", "back")


class HapticPlayer:
    def __init__(
        self,
        haptics: Any,
        request_fn: Callable[[int], tuple[str, Any] | None],
        hat_on_fn: Callable[[], bool],
        recorder: TraceRecorder | None = None,
    ) -> None:
        self.haptics = haptics
        self.request_fn = request_fn
        self.hat_on_fn = hat_on_fn
        self.rec = recorder
        self.level = dict.fromkeys(MOTORS, 0.0)
        self._ack_until = 0
        self._ack_seg: dict[str, Any] | None = None
        self._pat: tuple[str, str] | None = None  # (motor, pattern name) of the running warning
        self._pat_seg: dict[str, Any] | None = None
        self._pat_origin = 0
        self.last_set_t_ns: int | None = None

    # ------------------------------------------------------------ driver output (edge-triggered)
    def _apply(self, want: dict[str, float], now_ns: int) -> None:
        for m in MOTORS:
            if want[m] != self.level[m]:
                self.level[m] = want[m]
                self.last_set_t_ns = now_ns
                try:
                    self.haptics.set(m, want[m])
                except Exception:  # noqa: BLE001 - the proxy already handles driver faults
                    pass

    def off_all(self, now_ns: int) -> None:
        self._apply(dict.fromkeys(MOTORS, 0.0), now_ns)

    def _close(self, seg: dict[str, Any] | None, now_ns: int) -> None:
        if seg is not None:
            seg["duration_s"] = max(0.0, (now_ns - seg["t_ns"]) * 1e-9)

    # ------------------------------------------------------------ events
    def on_detection(self, det: Detection, now_ns: int) -> None:
        if det.type in ACK_TYPES and self.hat_on_fn():
            if now_ns >= self._ack_until:  # one buzz per event, not one per repeated detection
                self._ack_until = now_ns + int(ACK_S * 1e9)
                self._ack_seg = {"t_ns": now_ns, "motor": "both", "pattern": "ack_1500ms", "duration_s": ACK_S}
                if self.rec:
                    self.rec.record_haptic(self._ack_seg)
        self.tick(now_ns)

    def tick(self, now_ns: int) -> None:
        if not self.hat_on_fn():
            self._ack_until = 0
            self._stop_pattern(now_ns)
            self.off_all(now_ns)
            return
        if now_ns < self._ack_until:
            self._stop_pattern(now_ns)
            self._apply(dict.fromkeys(MOTORS, 1.0), now_ns)
            return
        if self._ack_seg is not None:
            self._close(self._ack_seg, now_ns)
            self._ack_seg = None
        req = self.request_fn(now_ns)
        if req is None:
            self._stop_pattern(now_ns)
            self.off_all(now_ns)
            return
        motor, rate = req
        name = "continuous" if rate == "continuous" else f"pulse_{round(float(rate))}hz"
        key = (motor, name)
        if key != self._pat:
            self._stop_pattern(now_ns)
            self._pat, self._pat_origin = key, now_ns
            self._pat_seg = {"t_ns": now_ns, "motor": motor, "pattern": name, "duration_s": 0.0}
            if self.rec:
                self.rec.record_haptic(self._pat_seg)
        self._close(self._pat_seg, now_ns)
        if rate == "continuous":
            on = True
        else:
            period = 1e9 / float(rate)
            on = ((now_ns - self._pat_origin) % period) < period / 2
        want = dict.fromkeys(MOTORS, 0.0)
        want[motor] = 1.0 if on else 0.0
        self._apply(want, now_ns)

    def _stop_pattern(self, now_ns: int) -> None:
        if self._pat is not None:
            self._close(self._pat_seg, now_ns)
        self._pat = self._pat_seg = None
