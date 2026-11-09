"""Shared helpers for the bridge tests: a config pointing at tmp dirs, a fake `argus_dir` and event writers."""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from argus.bridge.clips import ClipPolicy
from argus.bridge.service import Bridge
from argus.config import DEFAULT_CONFIG_PATH, ArgusConfig, parse_config

SAMPLES = Path(__file__).parents[2] / "pi_bringup" / "samples"
FAST = ClipPolicy(wait_s=3.0, poll_s=0.02, stable_s=0.1, h264_grace_s=0.3)
T0 = datetime(2026, 11, 8, 22, 0, 0, tzinfo=UTC)


def make_cfg(tmp_path: Path, **bridge: Any) -> ArgusConfig:
    raw = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text())
    argus = tmp_path / "argus"
    (argus / "clips").mkdir(parents=True, exist_ok=True)
    raw["events"]["queue_dir"] = str(tmp_path / "queue")
    raw["device"]["serial"] = "ARG-0001"
    raw["bridge"].update({"argus_dir": str(argus), "air_log": str(argus / "air.jsonl"),
                          "state_dir": str(tmp_path / "state"), "launch": False, "clip_wait_s": 3.0, **bridge})
    return parse_config(raw)


class Hat:
    """A pretend `hat.main`: appends events to events.jsonl and drops clips like their camera does."""

    def __init__(self, cfg: ArgusConfig) -> None:
        self.dir = Path(cfg.bridge.argus_dir)
        self.events = self.dir / "events.jsonl"
        self.clips = self.dir / "clips"

    def append(self, **over: Any) -> dict[str, Any]:
        ev = json.loads((SAMPLES / "fall.sample.json").read_text())
        ev["event_id"] = str(uuid.uuid4())
        ev.update(over)
        with self.events.open("a") as f:
            f.write(json.dumps(ev) + "\n")
        return ev

    def fall(self, when: datetime = T0, **over: Any) -> dict[str, Any]:
        return self.append(type="fall", timestamp=when.isoformat(timespec="milliseconds"), **over)

    def lof(self, when: datetime = T0, side: str = "left", **over: Any) -> dict[str, Any]:
        ev = json.loads((SAMPLES / "line_of_fire.sample.json").read_text())
        ev.update(event_id=str(uuid.uuid4()), timestamp=when.isoformat(timespec="milliseconds"), side=side, **over)
        with self.events.open("a") as f:
            f.write(json.dumps(ev) + "\n")
        return ev

    def mp4(self, event_id: str, size: int = 5000) -> Path:
        p = self.clips / f"{event_id}.mp4"
        p.write_bytes(b"\x00" * size)
        return p

    def mp4_late(self, event_id: str, after_s: float) -> threading.Thread:
        def go() -> None:
            time.sleep(after_s)
            with (self.clips / f"{event_id}.mp4").open("wb") as f:  # grows for a while, like an ffmpeg remux
                for _ in range(5):
                    f.write(b"\x00" * 1000)
                    f.flush()
                    time.sleep(0.05)
        t = threading.Thread(target=go)
        t.start()
        return t


@pytest.fixture
def cfg(tmp_path: Path) -> ArgusConfig:
    return make_cfg(tmp_path)


@pytest.fixture
def hat(cfg: ArgusConfig) -> Hat:
    return Hat(cfg)


class Clock:
    """Controllable wall + monotonic clocks for the hat-off timing tests."""

    def __init__(self, wall: datetime = T0) -> None:
        self.wall, self.mono = wall, 1000.0

    def advance(self, s: float) -> None:
        self.wall += timedelta(seconds=s)
        self.mono += s


def make_bridge(cfg: ArgusConfig, clock: Clock | None = None, **kw: Any) -> Bridge:
    extra: dict[str, Any] = {}
    if clock:
        extra = {"wall_fn": lambda: clock.wall, "mono_fn": lambda: clock.mono}
    return Bridge(cfg, launch=False, policy=FAST, poll_s=0.02, **extra, **kw)


def settle(b: Bridge, timeout: float = 15.0) -> None:
    """Run step() and wait for every worker to finish."""
    b.step()
    deadline = time.monotonic() + timeout
    for f in list(b.futures):
        f.result(timeout=max(0.1, deadline - time.monotonic()))


def queued(cfg: ArgusConfig) -> dict[str, dict[str, Any]]:
    q = Path(cfg.events.queue_dir)
    out = {}
    for d in sorted(q.iterdir()) if q.exists() else []:
        if (d / "READY").exists():
            out[d.name] = json.loads((d / "event.json").read_text())
    return out
