"""Replay hat_on from a .jsonl recording (see ../FORMAT.md). Options: file, speed, loop."""

from __future__ import annotations

from argus.contracts import HatOnSensor, ModuleContext
from hardware.replay.player import ReplayStream


class Replay(ReplayStream):
    stream = "tof"


def create(ctx: ModuleContext) -> HatOnSensor:
    return Replay(ctx)
