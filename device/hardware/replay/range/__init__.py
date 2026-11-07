"""Replay range from a .jsonl recording (see ../FORMAT.md). Options: file, speed, loop."""

from __future__ import annotations

from argus.contracts import ModuleContext, RangeArray
from hardware.replay.player import ReplayStream


class Replay(ReplayStream):
    stream = "range"


def create(ctx: ModuleContext) -> RangeArray:
    return Replay(ctx)
