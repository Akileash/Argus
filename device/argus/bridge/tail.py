"""Tail ``events.jsonl`` from a saved byte offset. At-least-once: events not yet enqueued stay in ``pending``."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("argus.bridge.tail")


class StateFile:
    """``{"offset", "pending": {event_id: raw event}, "seen": [event_id]}``, written atomically (tmp + fsync + rename)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset: int | None = None  # None = never run before
        self.pending: dict[str, dict[str, Any]] = {}
        self.head: str | None = None  # hash of the file's first line: a different one = rotated / rewritten file
        self.seen: list[str] = []  # last event ids handed to the queue (dedupe across restarts)
        try:
            d = json.loads(path.read_text())
            self.offset = int(d["offset"])
            self.pending = dict(d.get("pending") or {})
            self.seen = list(d.get("seen") or [])
            self.head = d.get("head")
        except FileNotFoundError:
            pass
        except (ValueError, KeyError, TypeError, OSError) as e:
            log.warning("bridge state %s unreadable (%s); treating as a first run", path, e)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        with tmp.open("w") as f:
            json.dump({"offset": self.offset, "pending": self.pending, "seen": self.seen[-1000:], "head": self.head}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)


class EventsTailer:
    """``poll()`` returns the new, complete JSON-object lines since the saved offset. Non-JSON lines are skipped.

    First run (no state): start at the END of the file so old test events are not uploaded, unless ``from_start``.
    A shrunken file (rotated or truncated) restarts at 0. A partial last line (no newline yet) waits for the rest.
    """

    def __init__(self, path: Path, state: StateFile, *, from_start: bool = False) -> None:
        self.path, self.state = path, state
        if state.offset is None:
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                size = 0
            state.offset = 0 if from_start else size
            state.save()
            log.info("first run: tailing %s from %s (offset %d)", path, "the start" if from_start else "the end", state.offset)

    def _head(self) -> str | None:
        """Hash of the first line, once it is complete (None while the file has no full line yet)."""
        try:
            with self.path.open("rb") as f:
                first = f.read(8192)
        except OSError:
            return None
        nl = first.find(b"\n")
        return None if nl < 0 else hashlib.sha1(first[:nl]).hexdigest()

    def poll(self) -> list[dict[str, Any]]:
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return []
        assert self.state.offset is not None
        head = self._head()
        if size < self.state.offset or (head is not None and self.state.head is not None and head != self.state.head):
            log.warning("%s was truncated or replaced (size %d, saved offset %d): restarting at 0", self.path, size,
                        self.state.offset)
            self.state.offset = 0
            self.state.head = None
            self.state.save()
        if head is not None and head != self.state.head:
            self.state.head = head
            self.state.save()
        if size == self.state.offset:
            return []
        with self.path.open("rb") as f:
            f.seek(self.state.offset)
            chunk = f.read()
        end = chunk.rfind(b"\n")
        if end < 0:
            return []
        out: list[dict[str, Any]] = []
        for line in chunk[: end + 1].splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                log.debug("skipping non-JSON line in %s", self.path)
                continue
            if isinstance(obj, dict):
                out.append(obj)
        self.state.offset += end + 1
        return out  # the caller records them in state.pending and then calls state.save()
