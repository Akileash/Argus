"""Team range adapter (spec §5.5). Config-driven: edit ``mapping.yaml`` here, not this file.

Contract: mm; None = no echo; sensor ids R2 and R3 for the MVP (spec §3.2a).

The shared engine ``hardware/team/_declarative.py`` runs your code and converts it using ``mapping.yaml`` (or
``modules.range.options`` in ``config.local.yaml``). Until a ``command`` / ``python`` source is set, ``create``
raises ``NotImplementedError`` so the supervisor falls back. If a mapping cannot express your hardware, write a
class here (see ``hardware/team/_adapter_base.py``) and return it from ``create``.
Check without hardware: ``argus-cli team check range --sample sample.txt``.
"""

from __future__ import annotations

from pathlib import Path

from argus.contracts import ModuleContext, RangeArray
from hardware.team._declarative import create as _create


def create(ctx: ModuleContext) -> RangeArray:
    return _create("range", ctx, Path(__file__).parent)
