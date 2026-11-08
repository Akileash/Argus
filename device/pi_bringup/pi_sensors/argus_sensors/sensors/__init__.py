"""Sensor plugins. Import the concrete classes from their modules."""

from __future__ import annotations

from typing import Dict, Type

from ..base import Sensor


def available_sensors() -> Dict[str, Type[Sensor]]:
    from .vl6180x import VL6180XWearSensor
    from .scd41 import SCD41Sensor
    from .mock import MockTofSensor, MockScdSensor

    return {
        "tof": VL6180XWearSensor,
        "scd41": SCD41Sensor,
        "mock_tof": MockTofSensor,
        "mock_scd41": MockScdSensor,
    }
