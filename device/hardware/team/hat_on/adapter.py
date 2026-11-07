"""Team hat_on adapter (spec §5.5). Config-driven: edit ``mapping.yaml`` here, not this file.

Contract: mm; status ok | out_of_range | error (VL53L0X reads ~8190 mm when nothing is in range).

The shared engine ``hardware/team/_declarative.py`` runs your code and converts it using ``mapping.yaml`` (or
``modules.hat_on.options`` in ``config.local.yaml``). Until a ``command`` / ``python`` source is set, ``create``
raises ``NotImplementedError`` so the supervisor falls back. If a mapping cannot express your hardware, write a
class here (see ``hardware/team/_adapter_base.py``) and return it from ``create``.
Check without hardware: ``argus-cli team check hat_on --sample sample.txt``.
"""

from __future__ import annotations

from pathlib import Path

from argus.contracts import HatOnSensor, ModuleContext
from hardware.team._declarative import create as _create


def create(ctx: ModuleContext) -> HatOnSensor:
    return _create("hat_on", ctx, Path(__file__).parent)
