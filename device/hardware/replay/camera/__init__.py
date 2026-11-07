"""Video is not part of .jsonl recordings: replay uses the mock camera (idle scenario)."""

from __future__ import annotations

from argus.contracts import Camera, ModuleContext
from hardware.mock.camera import create as _create_mock


def create(ctx: ModuleContext) -> Camera:
    return _create_mock(ModuleContext(ctx.clock, ctx.options, None))
