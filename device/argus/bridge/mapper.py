"""Pure mapping: one event from the teammates' ``events.jsonl`` -> ``contracts/event.schema.json`` + trace.

Nothing here touches the disk or the clock, so it is tested by replaying their sample files.
Mapping table: spec section 6; decisions D3, D4, D6, D7, D8.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# Their event types the bridge turns into events; everything else (e.g. ``impact``) is ignored.
HAT_EVENT_TYPES = ("fall", "line_of_fire")
OUR_TYPE = {"fall": "fall", "line_of_fire": "struck_by_near_miss"}
OUR_CLASS = {"fall": "incident", "struck_by_near_miss": "near_miss", "hat_knocked_off": "incident",
             "hat_removed": "ppe_log"}
NEAR_MISS_LINK_S = 2.0  # D4: a fall within this after a near miss links back to it
SATURATION_G = 15.9
G = 9.81
FIRMWARE_VERSION = "bridge-0.1"
CLIP_PRE_S = 20.0  # hat/config.py CAM_PRE_S / CAM_POST_S: the clip they cut around a fall
CLIP_POST_S = 10.0
METRIC_KEYS = ("peak_g", "peak_g_saturated", "closing_rate_mps", "ttc_s", "range_m", "sensor_id", "free_fall_ms",
               "drop_height_m", "hat_on")


def parse_ts(s: str) -> datetime:
    """Their ``timestamp`` (local time with offset) -> aware UTC datetime. A naive value is taken as UTC."""
    dt = datetime.fromisoformat(s)
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


@dataclass
class Mapped:
    """The result of mapping: an event dict without ``media`` (the queue fills it), plus the trace (or None)."""

    event: dict[str, Any]
    trace: dict[str, Any] | None
    wants_clip: bool
    notes: list[str] = field(default_factory=list)  # integration problems seen on this event (logged by the caller)


def is_hat_event(raw: Any) -> bool:
    return (isinstance(raw, dict) and raw.get("type") in HAT_EVENT_TYPES
            and isinstance(raw.get("event_id"), str) and isinstance(raw.get("timestamp"), str))


def side_sensor(side: Any, side_map: dict[str, str]) -> str | None:
    s = side_map.get(side) if isinstance(side, str) else None
    return s if s in ("R1", "R2", "R3", "R4", "TOP") else None


def _env(air: dict[str, Any] | None) -> dict[str, Any] | None:
    if not air:
        return None
    return {k: _num(air.get(k)) for k in ("co2_ppm", "temp_c", "rh_pct")}


def _base(event_id: str, typ: str, t0: datetime, detected: datetime, serial: str, env: dict | None) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "event_id": event_id,
        "device_serial": serial,
        "type": typ,
        "device_class": OUR_CLASS[typ],
        "t0": iso_utc(t0),
        "detected_at": iso_utc(detected),
        "window": {"pre_s": 0.0, "post_s": 0.0},
        "metrics": {},
        "env": env,
        "merged_triggers": [],
        "media": [],
        "firmware": {"version": FIRMWARE_VERSION,
                     "impls": {"imu": "team", "range": "team", "hat_on": "team", "haptics": "team",
                               "camera": "team", **({"env": "team"} if env else {})},
                     "fallbacks_active": []},
    }


def _metrics(**kw: Any) -> dict[str, Any]:
    m: dict[str, Any] = {"peak_g": None, "peak_g_saturated": False, "closing_rate_mps": None, "ttc_s": None,
                         "range_m": None, "sensor_id": None, "free_fall_ms": None, "drop_height_m": None,
                         "hat_on": True}
    m.update(kw)
    return m


def fall_metrics(raw: dict[str, Any]) -> dict[str, Any]:
    s = raw.get("sensor") or {}
    peak, ff = _num(s.get("peak_accel_g")), _num(s.get("freefall_ms"))
    worn = s.get("helmet_worn_at_onset")
    return _metrics(
        peak_g=peak, peak_g_saturated=bool(peak is not None and peak >= SATURATION_G),
        free_fall_ms=ff, drop_height_m=None if ff is None else round(0.5 * G * (ff / 1000.0) ** 2, 3),
        # Their fall is wear-gated, so a fall that reached us was worn; absent means "not reported".
        hat_on=worn if isinstance(worn, bool) else True)


def _lof_metrics(raw: dict[str, Any], side_map: dict[str, str], hat_on: bool) -> dict[str, Any]:
    s = raw.get("sensor") or {}
    rng, closing = _num(s.get("distance_m")), _num(s.get("closing_speed_mps"))
    ttc = round(rng / closing, 3) if rng is not None and closing is not None and closing > 0 else None
    return _metrics(range_m=rng, closing_rate_mps=closing, ttc_s=ttc, hat_on=hat_on,
                    sensor_id=side_sensor(raw.get("side"), side_map))


def imu_rate_hz(trace: list[list[float]]) -> float | None:
    """Measured sample rate of their imu_trace (median dt). Never assumed."""
    ts = [r[0] for r in trace]
    dts = sorted(b - a for a, b in itertools.pairwise(ts) if b > a)
    return None if not dts else round(1000.0 / dts[len(dts) // 2], 1)


def _clean_imu(raw_trace: Any) -> tuple[list[list[float]], int]:
    rows, bad = [], 0
    for r in raw_trace if isinstance(raw_trace, list) else []:
        vals = [_num(v) for v in r] if isinstance(r, (list, tuple)) and len(r) >= 4 else None
        if vals is None or None in vals:
            bad += 1
            continue
        rows.append([float(v) for v in vals[:4]])
    rows.sort(key=lambda r: r[0])
    return rows, bad


def build_trace(event_id: str, typ: str, raw: dict[str, Any], metrics: dict[str, Any], *, offset_s: float = 0.0,
                air: dict[str, Any] | None = None) -> dict[str, Any]:
    """contracts/sensor_trace.schema.json from what their event really holds.

    IMU: their ``imu_trace`` is ``[t_ms, ax, ay, az]`` relative to the event time. Gyro is not in it, so gx/gy/gz are
    empty arrays (the schema requires the keys; faking zeros would be inventing data). ``offset_s`` shifts times when
    the trace is reused by an event whose t0 differs (hat_knocked_off). Range: the single reading their event holds.
    """
    s = raw.get("sensor") or {}
    rows, _ = _clean_imu(s.get("imu_trace"))
    t = [round(r[0] / 1000.0 - offset_s, 4) for r in rows]
    ax, ay, az = ([round(r[i], 4) for r in rows] for i in (1, 2, 3))
    mag = [round(math.sqrt(x * x + y * y + z * z), 4) for x, y, z in zip(ax, ay, az, strict=True)]
    peaks = [{"t": tt, "a_mag_g": round(m, 3), "saturated": m >= SATURATION_G or max(abs(a), abs(b), abs(c)) >= SATURATION_G}
             for tt, m, a, b, c in zip(t, mag, ax, ay, az, strict=True) if m >= 3.0][:2000]
    det: dict[str, Any] = {"t": round(-offset_s, 4), "type": typ, "source": "team_hat"}
    for k in ("sensor_id", "peak_g", "peak_g_saturated", "closing_rate_mps", "ttc_s", "range_m", "free_fall_ms",
              "drop_height_m"):
        v = metrics.get(k)
        if v is not None and v is not False:
            det[k] = v
    rate = imu_rate_hz(rows)
    if rate is not None:
        det["imu_rate_hz"] = rate
        det["imu_trace_span_s"] = round(t[-1] - t[0], 3)
    if mag:
        det["peak_t_s"] = t[max(range(len(mag)), key=mag.__getitem__)]
    ranges: dict[str, Any] = {}
    if metrics.get("sensor_id") and metrics.get("range_m") is not None:
        ranges[metrics["sensor_id"]] = {"t": [round(-offset_s, 4)], "range_m": [metrics["range_m"]],
                                        "closing_mps": [metrics.get("closing_rate_mps")]}
    t_all = t + [round(-offset_s, 4)]
    return {
        "schema_version": "1", "event_id": event_id, "t_start_s": min(t_all), "t_end_s": max(t_all),
        "imu": {"t": t, "a_mag_g": mag, "ax": ax, "ay": ay, "az": az, "gx": [], "gy": [], "gz": []},
        "imu_peaks": peaks, "ranges": ranges, "tof": {"t": [], "range_mm": []},
        "hat_on": [{"t": min(t_all), "on": bool(metrics.get("hat_on", True))}],
        "haptics": [], "detections": [det], "env": _env(air),
    }


def map_hat_event(raw: dict[str, Any], *, serial: str, side_map: dict[str, str], air: dict[str, Any] | None = None,
                  hat_on: bool = True, recent_near_misses: list[tuple[str, datetime]] | None = None,
                  clip_s: float | None = None) -> Mapped:
    """Map a ``fall`` / ``line_of_fire`` event. ``clip_s`` (the real clip length) is set later via ``with_clip``."""
    t0 = parse_ts(raw["timestamp"])
    typ = OUR_TYPE[raw["type"]]
    ev = _base(raw["event_id"], typ, t0, t0, serial, _env(air))
    notes: list[str] = []
    if raw["type"] == "fall":
        ev["metrics"] = fall_metrics(raw)
        for _eid, nm_t in recent_near_misses or []:
            if 0 <= (t0 - nm_t).total_seconds() <= NEAR_MISS_LINK_S:  # D4
                ev["merged_triggers"].append({"type": "struck_by_near_miss", "offset_s": round((nm_t - t0).total_seconds(), 3)})
        rows, bad = _clean_imu((raw.get("sensor") or {}).get("imu_trace"))
        if not rows:
            notes.append("fall has no usable imu_trace; the trace has no IMU series")
        if bad:
            notes.append(f"{bad} imu_trace rows dropped (not [t_ms, ax, ay, az] numbers)")
    else:
        ev["metrics"] = _lof_metrics(raw, side_map, hat_on)
        if ev["metrics"]["sensor_id"] is None:
            notes.append(f"side {raw.get('side')!r} has no sensor_id mapping (bridge.side_sensor)")
    trace = build_trace(raw["event_id"], typ, raw, ev["metrics"], air=air)
    return Mapped(ev, trace, True, notes)


def with_clip(ev: dict[str, Any], clip_s: float | None, *, offset_s: float = 0.0) -> None:
    """Fill ``window`` from the real clip: it starts CLIP_PRE_S (+offset) before t0 and is ``clip_s`` long.
    ``offset_s`` = seconds t0 lies after the event the clip was cut for (shared clip of hat_knocked_off)."""
    if clip_s is None:
        ev["window"] = {"pre_s": 0.0, "post_s": 0.0}
        return
    pre = min(max(0.0, CLIP_PRE_S + offset_s), clip_s)
    ev["window"] = {"pre_s": round(pre, 3), "post_s": round(max(0.0, clip_s - pre), 3)}


def map_hat_off(*, event_id: str, off_start: datetime, detected: datetime, serial: str, fall: dict[str, Any] | None,
                fall_t0: datetime | None, air: dict[str, Any] | None) -> Mapped:
    """HELMET OFF -> ``hat_knocked_off`` (``fall`` given: shares its metrics and trace) or ``hat_removed`` (PPE log)."""
    if fall is None or fall_t0 is None:
        ev = _base(event_id, "hat_removed", off_start, detected, serial, _env(air))
        ev["metrics"] = _metrics(hat_on=False)
        return Mapped(ev, None, False)
    ev = _base(event_id, "hat_knocked_off", off_start, detected, serial, _env(air))
    ev["metrics"] = {**fall_metrics(fall), "hat_on": False}
    off = (off_start - fall_t0).total_seconds()
    trace = build_trace(event_id, "hat_knocked_off", fall, ev["metrics"], offset_s=off, air=air)
    trace["hat_on"] = [{"t": trace["t_start_s"], "on": True}, {"t": 0.0, "on": False}]
    ev["merged_triggers"] = []
    return Mapped(ev, trace, True)
