"""Replay imu from a .jsonl recording (see ../FORMAT.md). Options: file, speed, loop."""

from __future__ import annotations

from argus.contracts import Imu, ModuleContext
from hardware.replay.player import ReplayStream


class Replay(ReplayStream):
    stream = "imu"


def create(ctx: ModuleContext) -> Imu:
    return Replay(ctx)
