"""Sensor-trace export for one event window (contracts/sensor_trace.schema.json) + the logs it needs."""

from __future__ import annotations

import math
import threading
from collections import deque
from typing import Any

from argus.bus import SampleBus
from argus.contracts import SENSOR_IDS

KEEP_S = 60.0
IMU_BUCKET_NS = 10_000_000  # 100 Hz
PEAK_G = 3.0
MAX_PEAKS = 2000


def clean(v: Any, nd: int = 4) -> Any:
    """JSON-safe copy: inf/nan -> None, floats rounded, tuples -> lists."""
    if isinstance(v, bool) or v is None or isinstance(v, (str, int)):
        return v
    if isinstance(v, float):
        return round(v, nd) if math.isfinite(v) else None
    if isinstance(v, (list, tuple)):
        return [clean(x, nd) for x in v]
    if isinstance(v, dict):
        return {str(k): clean(x, nd) for k, x in v.items()}
    return str(v)


class TraceRecorder:
    """Thread-safe rolling logs: detections, per-sample closing rate, hat_on changes, haptic segments."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.detections: deque[Any] = deque()
        self.closing: dict[tuple[str, int], float] = {}
        self._closing_order: deque[tuple[str, int]] = deque()
        self.hat_changes: deque[tuple[int, bool]] = deque()
        self.haptics: deque[dict[str, Any]] = deque()
        self.initial_hat_on = True

    def record_detection(self, det: Any) -> None:
        with self._lock:
            self.detections.append(det)
            cutoff = det.t_ns - int(KEEP_S * 1e9)
            while self.detections and self.detections[0].t_ns < cutoff:
                self.detections.popleft()
        v = det.debug.get("v_obj_mps") if det.sensor_id and det.debug else None
        if v is not None:
            self.record_closing(det.sensor_id, det.t_ns, v)

    def record_closing(self, sensor_id: str, t_ns: int, v_obj: float) -> None:
        if not math.isfinite(v_obj):
            return
        with self._lock:
            key = (sensor_id, t_ns)
            if key not in self.closing:
                self._closing_order.append(key)
            self.closing[key] = v_obj
            while len(self._closing_order) > 4000:
                self.closing.pop(self._closing_order.popleft(), None)

    def record_hat(self, t_ns: int, on: bool) -> None:
        with self._lock:
            self.hat_changes.append((t_ns, on))
            while len(self.hat_changes) > 500:
                self.hat_changes.popleft()

    def record_haptic(self, seg: dict[str, Any]) -> None:
        """``seg``: {t_ns, motor, pattern, duration_s}; the player may keep updating duration_s in place."""
        with self._lock:
            self.haptics.append(seg)
            while len(self.haptics) > 500:
                self.haptics.popleft()


def _imu_columns(samples: list[Any], t0_ns: int, saturation_g: float) -> tuple[dict, list[dict]]:
    cols: dict[str, list[float]] = {k: [] for k in ("t", "a_mag_g", "ax", "ay", "az", "gx", "gy", "gz")}
    peaks: list[dict[str, Any]] = []
    best: Any = None
    best_mag = -1.0
    bucket = None

    def flush() -> None:
        if best is None:
            return
        cols["t"].append(round((best.t_ns - t0_ns) * 1e-9, 4))
        cols["a_mag_g"].append(round(best_mag, 4))
        for k in ("ax", "ay", "az"):
            cols[k].append(round(getattr(best, k), 4))
        for k in ("gx", "gy", "gz"):
            cols[k].append(round(getattr(best, k), 2))

    for s in samples:
        mag = math.sqrt(s.ax * s.ax + s.ay * s.ay + s.az * s.az)
        if mag >= PEAK_G and len(peaks) < MAX_PEAKS:
            sat = max(abs(s.ax), abs(s.ay), abs(s.az)) >= saturation_g
            peaks.append({"t": round((s.t_ns - t0_ns) * 1e-9, 4), "a_mag_g": round(mag, 3), "saturated": sat})
        b = s.t_ns // IMU_BUCKET_NS
        if b != bucket:
            flush()
            bucket, best, best_mag = b, s, mag
        elif mag > best_mag:  # keep the strongest sample of each 10 ms bucket so impacts survive
            best, best_mag = s, mag
    flush()
    return cols, peaks


def build_trace(
    event_id: str, t0_ns: int, t_start_ns: int, t_end_ns: int, bus: SampleBus, rec: TraceRecorder,
    saturation_g: float = 15.9,
) -> dict[str, Any]:
    def rel(t: int) -> float:
        return round((t - t0_ns) * 1e-9, 4)

    imu, peaks = _imu_columns(bus.window("imu", t_start_ns, t_end_ns), t0_ns, saturation_g)

    by_sensor: dict[str, list[Any]] = {k: [] for k in SENSOR_IDS}
    for s in bus.window("range", t_start_ns, t_end_ns):
        by_sensor[s.sensor_id].append(s)
    with rec._lock:
        closing = dict(rec.closing)
        dets = [d for d in rec.detections if t_start_ns <= d.t_ns <= t_end_ns]
        changes = list(rec.hat_changes)
        haptics = [dict(h) for h in rec.haptics]
    ranges: dict[str, Any] = {}
    for sid, ss in by_sensor.items():
        if not ss:
            continue
        ranges[sid] = {
            "t": [rel(s.t_ns) for s in ss],
            "range_m": [None if s.range_mm is None else round(s.range_mm / 1000.0, 4) for s in ss],
            "closing_mps": [None if (v := closing.get((sid, s.t_ns))) is None else round(v, 3) for s in ss],
        }

    tof = bus.window("tof", t_start_ns, t_end_ns)
    tof_doc = {"t": [rel(s.t_ns) for s in tof],
               "range_mm": [None if (s.status != "ok" or s.range_mm is None) else round(s.range_mm, 1) for s in tof]}

    state = rec.initial_hat_on
    for t, on in changes:
        if t <= t_start_ns:
            state = on
    hat = [{"t": rel(t_start_ns), "on": state}] + [
        {"t": rel(t), "on": on} for t, on in changes if t_start_ns < t <= t_end_ns]

    hap = []
    for h in haptics:
        end = h["t_ns"] + int(h.get("duration_s", 0.0) * 1e9)
        if end >= t_start_ns and h["t_ns"] <= t_end_ns:
            hap.append({"t": rel(h["t_ns"]), "motor": h["motor"], "pattern": h["pattern"],
                        "duration_s": round(h.get("duration_s", 0.0), 3)})

    det_docs = []
    for d in dets:
        doc: dict[str, Any] = {"t": rel(d.t_ns), "type": d.type, "sensor_id": d.sensor_id}
        for k in ("peak_g", "peak_g_saturated", "closing_rate_mps", "ttc_s", "range_m", "free_fall_ms",
                  "drop_height_m"):
            v = getattr(d, k)
            if v is not None and v is not False:
                doc[k] = clean(v)
        if d.debug:
            doc["debug"] = clean(d.debug)
        det_docs.append(doc)

    env = bus.latest("env")
    return {
        "schema_version": "1", "event_id": event_id, "t_start_s": rel(t_start_ns), "t_end_s": rel(t_end_ns),
        "imu": imu, "imu_peaks": peaks, "ranges": ranges, "tof": tof_doc, "hat_on": hat, "haptics": hap,
        "detections": det_docs,
        "env": None if env is None else {"co2_ppm": env.co2_ppm, "temp_c": env.temp_c, "rh_pct": env.rh_pct},
    }
