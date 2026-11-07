"""Team camera adapter (spec §5.5). Config-driven: edit ``mapping.yaml`` here, not this file.

Contract: a rolling buffer (>= 30 s) on the shared clock and save_clip(t_start_ns, t_end_ns, path) writing a real MP4 (spec §3.1).

The shared engine ``hardware/team/_declarative.py`` runs your code and converts it using ``mapping.yaml`` (or
``modules.camera.options`` in ``config.local.yaml``). Until a ``command`` / ``python`` source is set, ``create``
raises ``NotImplementedError`` so the supervisor falls back. If a mapping cannot express your hardware, write a
class here (see ``hardware/team/_adapter_base.py``) and return it from ``create``.
Check without hardware: ``argus-cli team check camera --sample sample.txt``.
"""

from __future__ import annotations

from pathlib import Path

from argus.contracts import Camera, ModuleContext
from hardware.team._declarative import create as _create


def create(ctx: ModuleContext) -> Camera:
    return _create("camera", ctx, Path(__file__).parent)
