"""Team env adapter (spec §5.5). Config-driven: edit ``mapping.yaml`` here, not this file.

Contract: co2 in ppm, temperature in deg C, humidity in %; ~0.2 Hz.

The shared engine ``hardware/team/_declarative.py`` runs your code and converts it using ``mapping.yaml`` (or
``modules.env.options`` in ``config.local.yaml``). Until a ``command`` / ``python`` source is set, ``create``
raises ``NotImplementedError`` so the supervisor falls back. If a mapping cannot express your hardware, write a
class here (see ``hardware/team/_adapter_base.py``) and return it from ``create``.
Check without hardware: ``argus-cli team check env --sample sample.txt``.
"""

from __future__ import annotations

from pathlib import Path

from argus.contracts import EnvSensor, ModuleContext
from hardware.team._declarative import create as _create


def create(ctx: ModuleContext) -> EnvSensor:
    return _create("env", ctx, Path(__file__).parent)
