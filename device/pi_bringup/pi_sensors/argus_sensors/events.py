"""NDJSON event builders. Field names match the Arduino / frontend contract."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

_START_MONO = time.monotonic()


def reset_clock() -> None:
    """Reset t_ms origin (tests only)."""
    global _START_MONO
    _START_MONO = time.monotonic()


def t_ms(now: Optional[float] = None) -> int:
    if now is None:
        now = time.monotonic()
    return int((now - _START_MONO) * 1000)


def received_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def stamp(evt: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
    """Add t_ms (if missing) and received_at."""
    out = dict(evt)
    if "t_ms" not in out:
        out["t_ms"] = t_ms(now)
    out["received_at"] = received_at()
    return out


def hello_tof(
    *,
    helmet_worn: bool,
    state: str,
    distance_mm: Optional[int],
    status: int,
    near: bool,
    sensor_ok: bool,
    recoveries: int,
    near_mm_min: int = 50,
    near_mm_max: int = 254,
    on_dwell_ms: int = 5000,
    off_dwell_ms: int = 3000,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    return stamp(
        {
            "type": "hello",
            "device": "vl6180x_wear",
            "near_mm_min": near_mm_min,
            "near_mm_max": near_mm_max,
            "on_dwell_ms": on_dwell_ms,
            "off_dwell_ms": off_dwell_ms,
            "helmet_worn": helmet_worn,
            "state": state,
            "distance_mm": distance_mm,
            "status": status,
            "near": near,
            "sensor_ok": sensor_ok,
            "recoveries": recoveries,
        },
        now,
    )


def sample_tof(
    *,
    helmet_worn: bool,
    state: str,
    distance_mm: Optional[int],
    status: int,
    near: bool,
    sensor_ok: bool,
    recoveries: int,
    on_in_ms: Optional[int] = None,
    off_in_ms: Optional[int] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    evt: Dict[str, Any] = {
        "type": "sample",
        "helmet_worn": helmet_worn,
        "state": state,
        "distance_mm": distance_mm,
        "status": status,
        "near": near,
        "sensor_ok": sensor_ok,
        "recoveries": recoveries,
    }
    if on_in_ms is not None:
        evt["on_in_ms"] = on_in_ms
    if off_in_ms is not None:
        evt["off_in_ms"] = off_in_ms
    return stamp(evt, now)


def wear_event(
    *,
    helmet_worn: bool,
    state: str,
    distance_mm: Optional[int],
    status: int,
    near: bool,
    sensor_ok: bool,
    recoveries: int,
    on_in_ms: Optional[int] = None,
    off_in_ms: Optional[int] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    evt: Dict[str, Any] = {
        "type": "wear_event",
        "helmet_worn": helmet_worn,
        "state": state,
        "distance_mm": distance_mm,
        "status": status,
        "near": near,
        "sensor_ok": sensor_ok,
        "recoveries": recoveries,
    }
    if on_in_ms is not None:
        evt["on_in_ms"] = on_in_ms
    if off_in_ms is not None:
        evt["off_in_ms"] = off_in_ms
    return stamp(evt, now)


def hello_scd(
    *,
    co2_ppm: Optional[int],
    temp_c: Optional[float],
    rh_pct: Optional[float],
    sensor_ok: bool,
    reads: int,
    recoveries: int,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    return stamp(
        {
            "type": "hello",
            "device": "scd41",
            "co2_ppm": co2_ppm,
            "temp_c": temp_c,
            "rh_pct": rh_pct,
            "sensor_ok": sensor_ok,
            "reads": reads,
            "recoveries": recoveries,
        },
        now,
    )


def sample_scd(
    *,
    co2_ppm: Optional[int],
    temp_c: Optional[float],
    rh_pct: Optional[float],
    sensor_ok: bool,
    reads: int,
    recoveries: int,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    return stamp(
        {
            "type": "sample",
            "co2_ppm": co2_ppm,
            "temp_c": temp_c,
            "rh_pct": rh_pct,
            "sensor_ok": sensor_ok,
            "reads": reads,
            "recoveries": recoveries,
        },
        now,
    )


def air_sample(
    *,
    co2_ppm: int,
    temp_c: float,
    rh_pct: float,
    sensor_ok: bool,
    reads: int,
    recoveries: int,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    return stamp(
        {
            "type": "air_sample",
            "co2_ppm": co2_ppm,
            "temp_c": round(temp_c, 2),
            "rh_pct": round(rh_pct, 2),
            "sensor_ok": sensor_ok,
            "reads": reads,
            "recoveries": recoveries,
        },
        now,
    )


def sensor_recover(*, ok: bool, recoveries: int, now: Optional[float] = None) -> Dict[str, Any]:
    return stamp({"type": "sensor_recover", "ok": ok, "recoveries": recoveries}, now)
