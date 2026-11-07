"""Team haptics adapter (spec §5.5). Config-driven: edit ``mapping.yaml`` here, not this file.

Contract: set(motor, intensity) with motor in {top, back}, intensity 0.0-1.0, and off_all(); patterns live in L3, not here (spec §2.1, §2.4).

The shared engine ``hardware/team/_declarative.py`` runs your code and converts it using ``mapping.yaml`` (or
``modules.haptics.options`` in ``config.local.yaml``). Until a ``command`` / ``python`` source is set, ``create``
raises ``NotImplementedError`` so the supervisor falls back. If a mapping cannot express your hardware, write a
class here (see ``hardware/team/_adapter_base.py``) and return it from ``create``.
Check without hardware: ``argus-cli team check haptics --sample sample.txt``.
"""

from __future__ import annotations

from pathlib import Path

from argus.contracts import HapticDriver, ModuleContext
from hardware.team._declarative import create as _create


def create(ctx: ModuleContext) -> HapticDriver:
    return _create("haptics", ctx, Path(__file__).parent)
