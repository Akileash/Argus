"""Virtual-clock harness: mock hat -> supervisor -> bus -> Pipeline -> on-disk queue."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from argus.bus import SampleBus
from argus.config import ArgusConfig, load_config
from argus.contracts import ClipInfo
from argus.pipeline import Pipeline
from argus.supervisor import Supervisor
from hardware.mock.clock import VirtualClock

EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
STREAM_MODULES = ("imu", "range", "hat_on", "env")


class StubCamera:
    """Writes a tiny fake mp4 (no ffmpeg); records the requested windows."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def save_clip(self, t_start_ns: int, t_end_ns: int, path: str) -> ClipInfo:
        self.calls.append((t_start_ns, t_end_ns))
        Path(path).write_bytes(b"\x00\x00\x00\x18ftypmp42stub")
        return ClipInfo(path, t_start_ns, t_end_ns, (t_end_ns - t_start_ns) / 1e9, 16)


@dataclass
class Run:
    sup: Supervisor
    pipe: Pipeline
    clock: VirtualClock
    bus: SampleBus
    cfg: ArgusConfig
    queue_dir: Path
    t0_ns: int
    fallbacks: list[dict] = field(default_factory=list)

    def advance(self, seconds: float, dt: float = 0.01) -> None:
        for _ in range(round(seconds / dt)):
            self.clock.advance(dt)
            for m in STREAM_MODULES:
                self.sup.poll_once(m)
            self.pipe.step()

    def t_s(self, iso: str) -> float:
        """Event timestamp -> scenario seconds."""
        return (datetime.fromisoformat(iso) - EPOCH).total_seconds()

    def events(self) -> list[dict[str, Any]]:
        return [json.loads((d / "event.json").read_text()) for d in self.pipe.queue.ready()]


def make_run(scenario: str, queue_dir: Path, *, stub_camera: bool = True, cfg: ArgusConfig | None = None) -> Run:
    cfg = cfg or load_config()
    clock = VirtualClock()
    bus = SampleBus(cfg.bus.history_s)
    sup = Supervisor(cfg, bus, clock, scenario=scenario)
    start_ns = clock.now_ns()

    def wall() -> datetime:  # wall clock that tracks the virtual clock: utc(t) == EPOCH + scenario seconds
        return EPOCH + timedelta(microseconds=(clock.now_ns() - start_ns) / 1000)

    pipe = Pipeline(sup, bus, cfg, queue_dir=str(queue_dir), wall_fn=wall, async_cut=False)
    sup.start(run_threads=False)
    if stub_camera:
        pipe.manager.camera = StubCamera()
    return Run(sup, pipe, clock, bus, cfg, queue_dir, clock.now_ns())
