#!/usr/bin/env python3
"""
Export Argus hat events into the backend POST /events JSON contract.

Design goals (hackathon data-transfer safety):
  - Exact, stable schema the backend can parse with Pydantic
  - Primary live types from hat: fall | line_of_fire
  - Legacy/compat types still accepted: impact | hat_removed
    (hat no longer emits impact or hat_removed — fall-only + ToF owns wear)
  - Idempotent event_id (safe to retry uploads)
  - Compact JSON, UTF-8, one file per event in outbox/
  - No multipart / frames required yet (keyframes added later)
  - Optional HTTP POST with retries; failures leave the file in outbox

Usage:
  cd ~/argus && source .venv/bin/activate
  python export_backend.py                     # convert events.jsonl → outbox/
  python export_backend.py --last 1            # newest only
  python export_backend.py --print             # print JSON to stdout
  python export_backend.py --post http://192.168.x.x:8000/events
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

ARGUS_DIR = Path(__file__).resolve().parent
EVENTS_PATH = ARGUS_DIR / "events.jsonl"
OUTBOX_DIR = ARGUS_DIR / "outbox"
SENT_DIR = ARGUS_DIR / "outbox_sent"

# Backend contract — keep these in sync with the FastAPI model.
# Live hat emitters: fall, line_of_fire. impact/hat_removed kept for old logs.
ALLOWED_TYPES = frozenset({"fall", "impact", "hat_removed", "line_of_fire"})

TYPE_MAP = {
    "fall": "fall",
    "impact": "impact",  # legacy only — current IMU emits fall for hard hits too
    "hat_removed": "hat_removed",  # legacy — ToF wear events are not this type
    "line_of_fire": "line_of_fire",
    "near_fall": "fall",  # map old near_fall labels to fall
}

DEFAULT_HAT_ID = "hat-01"
DEFAULT_WORKER_ID = "W-001"
DEFAULT_ZONE = "DEMO"

# Keyframe offsets relative to trigger (ms). Empty until camera is wired.
DEFAULT_FRAME_TIMES_MS: List[int] = []


def load_raw_events(path: Path) -> List[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Missing {path}")
    out: List[dict] = []
    with path.open() as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            if line.startswith("EVENT "):
                line = line[6:].strip()
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"WARNING: skip line {i}: {e}", file=sys.stderr)
    return out


def _num(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _clean_trace(trace: Any, max_points: int = 400) -> List[List[float]]:
    """
    Keep imu_trace as [[t_ms, ax, ay, az], ...] with finite floats only.
    Cap length so multipart/JSON stays small over flaky Wi-Fi.
    """
    if not isinstance(trace, list):
        return []
    cleaned: List[List[float]] = []
    for row in trace:
        if not isinstance(row, (list, tuple)) or len(row) < 4:
            continue
        try:
            t_ms, ax, ay, az = float(row[0]), float(row[1]), float(row[2]), float(row[3])
        except (TypeError, ValueError):
            continue
        cleaned.append([round(t_ms, 1), round(ax, 3), round(ay, 3), round(az, 3)])
    if len(cleaned) > max_points:
        # even downsample
        step = max(1, len(cleaned) // max_points)
        cleaned = cleaned[::step][:max_points]
    return cleaned


def to_backend_event(
    raw: dict,
    *,
    worker_id: str = DEFAULT_WORKER_ID,
    zone: str = DEFAULT_ZONE,
) -> dict:
    """
    Map a hat detector event → backend POST /events JSON body.

    Required top-level keys match the team contract. Extra hat-only
    diagnostics live under sensor.detail so a strict schema can ignore
    or accept them without breaking required fields.
    """
    raw_type = str(raw.get("type", "fall"))
    etype = TYPE_MAP.get(raw_type, "fall")
    if etype not in ALLOWED_TYPES:
        etype = "fall"

    sensor_in = raw.get("sensor") or {}
    peak_g = _num(sensor_in.get("peak_accel_g"))
    freefall_ms = _num(sensor_in.get("freefall_ms"))

    # Contract sensor block (always present, always the same keys)
    sensor: Dict[str, Any] = {
        "distance_m": None,          # null until HC-SR04 is integrated
        "closing_speed_mps": None,   # null until HC-SR04 is integrated
        "peak_accel_g": round(peak_g, 3),
        "freefall_ms": round(freefall_ms, 1),
        "imu_trace": _clean_trace(sensor_in.get("imu_trace")),
    }

    # Optional, namespaced extras — backend should ignore unknown keys
    # under detail, or model them as Optional
    detail = {
        "peak_gyro_dps": round(_num(sensor_in.get("peak_gyro_dps")), 2),
        "tilt_change_deg": round(_num(sensor_in.get("tilt_change_deg")), 1),
        "still_after": bool(sensor_in.get("still_after", False)),
        "score": round(_num(sensor_in.get("score")), 3),
        "hat_source_type": raw_type,  # original hat label before TYPE_MAP
        "hat_possibly_set_down": bool(raw.get("hat_possibly_set_down", False)),
        "worn_before": bool(raw.get("worn_before", False)),
        "worn_after": bool(raw.get("worn_after", False)),
        "worn_confidence": raw.get("worn_confidence", "low"),
        "detector_confidence": raw.get("confidence", "low"),
        "priority": raw.get("priority", "low"),
        # Wear-gated fall fields (current hat IMU → ToF lookback)
        "helmet_worn_at_onset": sensor_in.get("helmet_worn_at_onset"),
        "helmet_worn_lookback_s": sensor_in.get("helmet_worn_lookback_s"),
        "wear_on_fraction": sensor_in.get("wear_on_fraction"),
    }
    sensor["detail"] = detail

    # LoF / US fields when present
    if sensor_in.get("distance_m") is not None:
        sensor["distance_m"] = round(_num(sensor_in.get("distance_m")), 3)
    if sensor_in.get("closing_speed_mps") is not None:
        sensor["closing_speed_mps"] = round(_num(sensor_in.get("closing_speed_mps")), 3)

    event_id = str(raw.get("event_id") or "")
    if not event_id:
        raise ValueError("raw event missing event_id")

    payload = {
        "event_id": event_id,
        "hat_id": str(raw.get("hat_id") or DEFAULT_HAT_ID),
        "worker_id": worker_id,
        "type": etype,
        "timestamp": str(raw.get("timestamp") or ""),
        "zone": zone,
        "sensor": sensor,
        "frame_times_ms": list(DEFAULT_FRAME_TIMES_MS),
        # Clip uploaded later via PUT /events/{id}/clip — not required here
        "clip_pending": True,
        "schema_version": 1,
    }

    validate_payload(payload)
    return payload


def validate_payload(p: dict) -> None:
    """Fail fast before writing/uploading bad JSON."""
    required = ["event_id", "hat_id", "worker_id", "type", "timestamp", "zone", "sensor", "frame_times_ms"]
    for k in required:
        if k not in p:
            raise ValueError(f"missing key: {k}")
    if p["type"] not in ALLOWED_TYPES:
        raise ValueError(f"bad type: {p['type']}")
    if not p["event_id"]:
        raise ValueError("empty event_id")
    if not p["timestamp"]:
        raise ValueError("empty timestamp")
    s = p["sensor"]
    for k in ("peak_accel_g", "freefall_ms", "imu_trace"):
        if k not in s:
            raise ValueError(f"sensor missing {k}")
    if not isinstance(s["imu_trace"], list):
        raise ValueError("imu_trace must be a list")
    if not isinstance(p["frame_times_ms"], list):
        raise ValueError("frame_times_ms must be a list")


def write_outbox(payload: dict, outbox: Path = OUTBOX_DIR) -> Path:
    outbox.mkdir(parents=True, exist_ok=True)
    path = outbox / f"{payload['event_id']}.json"
    # Compact separators → smaller Wi-Fi payloads; ensure_ascii for boring parsers
    text = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    path.write_text(text + "\n", encoding="utf-8")
    return path


def post_event(url: str, payload: dict, timeout_s: float = 10.0) -> int:
    """
    POST JSON body. Backend should treat duplicate event_id as 200 no-op.
    Returns HTTP status.
    """
    data = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "X-Argus-Schema": "1",
            "X-Event-Id": payload["event_id"],
        },
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return int(resp.status)


def post_with_retries(url: str, payload: dict, attempts: int = 3) -> bool:
    last_err: Optional[Exception] = None
    for i in range(attempts):
        try:
            status = post_event(url, payload)
            if 200 <= status < 300:
                return True
            print(f"  POST status {status}, retrying...", file=sys.stderr)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last_err = e
            print(f"  POST attempt {i + 1}/{attempts} failed: {e}", file=sys.stderr)
        time.sleep(0.5 * (2 ** i))
    if last_err:
        print(f"  giving up: {last_err}", file=sys.stderr)
    return False


def mark_sent(path: Path) -> None:
    SENT_DIR.mkdir(parents=True, exist_ok=True)
    dest = SENT_DIR / path.name
    path.replace(dest)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Export hat events for Argus backend")
    ap.add_argument("--events", default=str(EVENTS_PATH), help="Source events.jsonl")
    ap.add_argument("--last", type=int, default=0, help="Only export last N events")
    ap.add_argument("--worker-id", default=DEFAULT_WORKER_ID)
    ap.add_argument("--zone", default=DEFAULT_ZONE)
    ap.add_argument("--print", action="store_true", help="Print payloads to stdout")
    ap.add_argument("--post", default="", help="POST each payload to this URL")
    ap.add_argument("--no-outbox", action="store_true", help="Do not write outbox files")
    args = ap.parse_args(argv)

    try:
        raw_events = load_raw_events(Path(args.events))
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if args.last > 0:
        raw_events = raw_events[-args.last :]

    if not raw_events:
        print("No events to export.")
        return 0

    ok = 0
    for raw in raw_events:
        try:
            payload = to_backend_event(
                raw, worker_id=args.worker_id, zone=args.zone
            )
        except (ValueError, KeyError) as e:
            print(f"ERROR converting {raw.get('event_id')}: {e}", file=sys.stderr)
            continue

        path = None
        if not args.no_outbox:
            path = write_outbox(payload)
            print(f"outbox ← {path.name}  type={payload['type']}  "
                  f"peak={payload['sensor']['peak_accel_g']}g")

        if args.print:
            print(json.dumps(payload, indent=2))

        if args.post:
            print(f"POST {payload['event_id']} → {args.post}")
            if post_with_retries(args.post, payload) and path is not None:
                mark_sent(path)
                print("  sent OK")
            else:
                print("  left in outbox for retry")
        ok += 1

    print(f"Done. {ok}/{len(raw_events)} event(s) exported.")
    print("Backend body shape: event_id, hat_id, worker_id, type, timestamp, zone,")
    print("  sensor{distance_m, closing_speed_mps, peak_accel_g, freefall_ms, imu_trace, detail},")
    print("  frame_times_ms, clip_pending, schema_version")
    return 0


if __name__ == "__main__":
    sys.exit(main())
