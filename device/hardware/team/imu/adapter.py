"""Team imu adapter (spec §5.5). Config-driven: edit ``mapping.yaml`` here, not this file.

Contract: accel in g (|a| ~ 1 at rest), gyro in deg/s, hat frame x forward / y left / z up; >= 400 Hz.

The shared engine ``hardware/team/_declarative.py`` runs your code and converts it using ``mapping.yaml`` (or
``modules.imu.options`` in ``config.local.yaml``). Until a ``command`` / ``python`` source is set, ``create``
raises ``NotImplementedError`` so the supervisor falls back. If a mapping cannot express your hardware, write a
class here (see ``hardware/team/_adapter_base.py``) and return it from ``create``.
Check without hardware: ``argus-cli team check imu --sample sample.txt``.
"""

from __future__ import annotations

from pathlib import Path

from argus.contracts import Imu, ModuleContext
from hardware.team._declarative import create as _create


def create(ctx: ModuleContext) -> Imu:
    return _create("imu", ctx, Path(__file__).parent)
