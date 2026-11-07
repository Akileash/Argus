"""``argus-cli record``: raw sensor streams -> ``recordings/<timestamp>_<label>.jsonl`` + labels sidecar.

Format: hardware/replay/FORMAT.md. Samples are appended as they arrive (a crash loses little); readers sort.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from argus.bus import STREAMS, SampleBus
from hardware.replay.recording import HEADER_KEY, sample_to_record

RECORDINGS_DIR = Path(__file__).resolve().parents[1] / "recordings"


def slug(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-") or "recording"


def parse_expect(spec: str) -> dict[str, Any]:
    """``type[:t_s[:sensor]]`` -> {type, t_s?, sensor?} (t_s = seconds from recording start)."""
    parts = spec.split(":")
    if not parts[0] or len(parts) > 3:
        raise ValueError(f"bad --expect {spec!r}; use type[:t_s[:sensor]]")
    out: dict[str, Any] = {"type": parts[0]}
    if len(parts) > 1 and parts[1]:
        out["t_s"] = float(parts[1])
    if len(parts) > 2 and parts[2]:
        out["sensor"] = parts[2]
    return out


def write_labels(path: Path, label: str, expected: list[dict[str, Any]], notes: str = "") -> Path:
    path.write_text(json.dumps({"label": label, "expected": expected, "notes": notes}, indent=2) + "\n")
    return path


class Recorder:
    def __init__(self, bus: SampleBus, out_dir: Path | str | None, label: str, *, meta: dict[str, Any] | None = None):
        out = Path(out_dir) if out_dir else RECORDINGS_DIR
        out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        self.path = out / f"{stamp}_{slug(label)}.jsonl"
        self.labels_path = self.path.with_suffix(".labels.json")
        self.bus = bus
        self.count = 0
        self._lock = threading.Lock()
        self._f = self.path.open("w")
        header = {"source": "argus-cli record", "label": label, "created": datetime.now(UTC).isoformat(), **(meta or {})}
        self._f.write(json.dumps({HEADER_KEY: header}) + "\n")
        self._unsubs = [bus.subscribe(s, self._on_sample) for s in STREAMS]

    def _on_sample(self, s: Any) -> None:
        line = json.dumps(sample_to_record(s)) + "\n"
        with self._lock:
            if not self._f.closed:
                self._f.write(line)
                self.count += 1

    def close(self) -> Path:
        for u in self._unsubs:
            u()
        with self._lock:
            self._f.close()
        return self.path
