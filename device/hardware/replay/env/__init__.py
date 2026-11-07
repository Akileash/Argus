"""Replay env from a .jsonl recording (see ../FORMAT.md). Options: file, speed, loop."""

from __future__ import annotations

from argus.contracts import EnvSensor, ModuleContext
from hardware.replay.player import ReplayStream


class Replay(ReplayStream):
    stream = "env"


def create(ctx: ModuleContext) -> EnvSensor:
    return Replay(ctx)
