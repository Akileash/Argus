"""Writer side of the on-disk event queue (contracts/device_queue.md). Atomic: tmp dir -> rename -> READY."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

log = logging.getLogger(__name__)

DEVICE_DIR = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = Path(os.environ.get("ARGUS_CONTRACTS_DIR") or DEVICE_DIR.parent / "contracts")


class SchemaError(ValueError):
    """A document does not satisfy its JSON Schema."""


@lru_cache(maxsize=4)
def _validator(name: str) -> Draft202012Validator | None:
    p = CONTRACTS_DIR / name
    if not p.exists():
        log.warning("schema %s not found (set ARGUS_CONTRACTS_DIR); skipping validation", p)
        return None
    return Draft202012Validator(json.loads(p.read_text()), format_checker=FormatChecker())


def _validate(name: str, doc: Any) -> None:
    v = _validator(name)
    if v is None:
        return
    errs = sorted(v.iter_errors(doc), key=lambda e: list(map(str, e.path)))
    if errs:
        raise SchemaError(f"{name}: " + "; ".join(
            f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errs[:5]))


def validate_event(doc: Any) -> None:
    _validate("event.schema.json", doc)


def validate_trace(doc: Any) -> None:
    _validate("sensor_trace.schema.json", doc)


def resolve_queue_dir(queue_dir: str | Path) -> Path:
    p = Path(queue_dir)
    return p if p.is_absolute() else DEVICE_DIR / p


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


class EventQueue:
    def __init__(self, root: str | Path, max_mb: float = 2048) -> None:
        self.root = resolve_queue_dir(root)
        self.max_bytes = int(max_mb * 1024 * 1024)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def tmp_dir(self, event_id: str) -> Path:
        d = self.root / f".tmp-{event_id}"
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        return d

    def commit(self, event: dict[str, Any], tmp: Path, clip_duration_s: float | None = None) -> Path:
        """Fill ``event['media']`` from the files in ``tmp``, validate, write event.json, rename, READY."""
        media: list[dict[str, Any]] = []
        clip, trace = tmp / "clip.mp4", tmp / "trace.json"
        if clip.exists():
            media.append({"kind": "clip", "camera": "front", "filename": "clip.mp4", "content_type": "video/mp4",
                          "bytes": clip.stat().st_size, "sha256": _sha256(clip), "duration_s": clip_duration_s})
        if trace.exists():
            media.append({"kind": "sensor_trace", "filename": "trace.json", "content_type": "application/json",
                          "bytes": trace.stat().st_size, "sha256": _sha256(trace), "duration_s": None})
        event["media"] = media
        try:
            validate_event(event)
        except SchemaError:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        (tmp / "event.json").write_text(json.dumps(event, indent=2))
        final = self.root / event["event_id"]
        os.rename(tmp, final)
        (final / "READY.tmp").touch()
        os.rename(final / "READY.tmp", final / "READY")
        self.enforce_disk_guard()
        return final

    def ready(self) -> list[Path]:
        """READY event dirs, oldest t0 first (the uploader's order)."""
        out = []
        for d in self.root.iterdir():
            if d.is_dir() and not d.name.startswith(".") and (d / "READY").exists():
                try:
                    t0 = json.loads((d / "event.json").read_text())["t0"]
                except (OSError, ValueError, KeyError):
                    continue
                out.append((t0, d))
        return [d for _, d in sorted(out)]

    def enforce_disk_guard(self) -> list[str]:
        """Over ``queue_max_mb``: drop the oldest clip.mp4 first (never event rows). Returns dropped ids."""
        dropped: list[str] = []
        with self._lock:
            while _dir_size(self.root) > self.max_bytes:
                victim = next((d for d in self.ready() if (d / "clip.mp4").exists()), None)
                if victim is None:
                    break
                (victim / "clip.mp4").unlink()
                st = victim / "upload_state.json"
                try:
                    state = json.loads(st.read_text()) if st.exists() else {}
                except ValueError:
                    state = {}
                state["clip_dropped"] = True
                tmp = victim / "upload_state.json.tmp"
                tmp.write_text(json.dumps(state))
                os.replace(tmp, st)
                dropped.append(victim.name)
                log.warning("disk guard: dropped clip of %s", victim.name)
        return dropped
