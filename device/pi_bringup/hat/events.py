"""Event builder + emit (print JSON, append events.jsonl)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from hat import config as cfg


def build_event(
    event_type: str,
    *,
    side: Optional[str] = None,
    distance_m: Optional[float] = None,
    closing_speed_mps: Optional[float] = None,
    peak_accel_g: Optional[float] = None,
    freefall_ms: Optional[float] = None,
    imu_trace: Optional[List[List[float]]] = None,
    confidence: Optional[str] = None,
    helmet_worn_at_onset: Optional[bool] = None,
    helmet_worn_lookback_s: Optional[float] = None,
    wear_on_fraction: Optional[float] = None,
) -> Dict[str, Any]:
    sensor: Dict[str, Any] = {}
    if event_type == "line_of_fire":
        if distance_m is not None:
            sensor["distance_m"] = distance_m
        if closing_speed_mps is not None:
            sensor["closing_speed_mps"] = closing_speed_mps
    elif event_type in ("fall", "impact"):
        if peak_accel_g is not None:
            sensor["peak_accel_g"] = peak_accel_g
        if freefall_ms is not None:
            sensor["freefall_ms"] = freefall_ms
        if imu_trace is not None:
            sensor["imu_trace"] = imu_trace
        if helmet_worn_at_onset is not None:
            sensor["helmet_worn_at_onset"] = helmet_worn_at_onset
        if helmet_worn_lookback_s is not None:
            sensor["helmet_worn_lookback_s"] = helmet_worn_lookback_s
        if wear_on_fraction is not None:
            sensor["wear_on_fraction"] = round(wear_on_fraction, 3)

    event: Dict[str, Any] = {
        "event_id": str(uuid.uuid4()),
        "hat_id": cfg.HAT_ID,
        "type": event_type,
        "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "side": side,
        "sensor": sensor,
    }
    if confidence is not None:
        event["confidence"] = confidence
    # Filled in later by ClipRecorder (path relative or absolute under clips/)
    event["clip_path"] = None
    event["clip_pending"] = False
    return event


def emit(event: Dict[str, Any]) -> None:
    """Print one clear JSON line and append to events.jsonl."""
    line = json.dumps(event, separators=(",", ":"))
    print(line, flush=True)
    cfg.EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with cfg.EVENTS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
