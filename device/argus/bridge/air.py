"""Latest SCD41 air sample from the pi_sensors log (read only), for the event's environment context (D8)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

TAIL_BYTES = 256 * 1024


def latest_air(path: Path, at: datetime, max_age_s: float) -> dict[str, Any] | None:
    """Newest ``air_sample`` received at or before ``at`` (+2 s of clock slack) and at most ``max_age_s`` older."""
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - TAIL_BYTES))
            data = f.read()
    except OSError:
        return None
    best: tuple[datetime, dict[str, Any]] | None = None
    for line in data.splitlines():
        try:
            d = json.loads(line)
            if d.get("type") != "air_sample" or d.get("sensor_ok") is False:
                continue
            ts = datetime.fromisoformat(d["received_at"])
            ts = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        if ts > at + timedelta(seconds=2) or (at - ts).total_seconds() > max_age_s:
            continue
        if best is None or ts > best[0]:
            best = (ts, d)
    return best[1] if best else None
