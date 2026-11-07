"""Read/write ``.jsonl`` sensor recordings (format: ``FORMAT.md``)."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from argus.contracts import EnvSample, ImuSample, RangeSample, TofSample

SAMPLE_CLASS: dict[str, type] = {"imu": ImuSample, "range": RangeSample, "tof": TofSample, "env": EnvSample}
STREAM_OF: dict[type, str] = {v: k for k, v in SAMPLE_CLASS.items()}
HEADER_KEY = "meta"


def sample_to_record(sample: Any) -> dict[str, Any]:
    stream = STREAM_OF[type(sample)]
    rec: dict[str, Any] = {"stream": stream}
    for f in type(sample).__dataclass_fields__:
        rec[f] = getattr(sample, f)
    return rec


def record_to_sample(rec: dict[str, Any]) -> Any:
    cls = SAMPLE_CLASS[rec["stream"]]
    return cls(**{f: rec.get(f) for f in cls.__dataclass_fields__})


def write_recording(path: Path | str, samples: Iterable[Any], meta: dict[str, Any] | None = None) -> Path:
    """Write samples (any mix of streams) sorted by ``t_ns``. ``meta`` becomes an optional first line."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w") as f:
        if meta:
            f.write(json.dumps({HEADER_KEY: meta}) + "\n")
        for s in sorted(samples, key=lambda s: s.t_ns):
            f.write(json.dumps(sample_to_record(s)) + "\n")
    return p


def read_recording(path: Path | str) -> Iterator[Any]:
    """Yield samples in file order; skips the optional ``meta`` header and blank lines."""
    with Path(path).open() as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if HEADER_KEY in rec:
                continue
            if rec.get("stream") not in SAMPLE_CLASS:
                raise ValueError(f"{path}:{n}: unknown stream {rec.get('stream')!r}")
            yield record_to_sample(rec)
