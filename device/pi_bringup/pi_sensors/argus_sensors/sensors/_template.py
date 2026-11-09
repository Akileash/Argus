"""Copy this file to add a new sensor plugin.

1. Rename the class and set `name` / `address`.
2. Implement init_sensor / poll / make_hello / make_sample.
3. Register it in sensors/__init__.py available_sensors().
4. Run: python -m argus_sensors --sensors my_sensor
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..base import Sensor
from ..bus import I2CBus
from .. import events
from ..sinks import Sink


class TemplateSensor(Sensor):
    name = "template"
    address = 0x00
    heartbeat_ms = 2000
    poll_gap_ms = 100.0
    fails_before_recover = 8
    recover_cooldown_ms = 2000

    def __init__(self, bus: I2CBus, sink: Sink) -> None:
        super().__init__(bus, sink)

    def init_sensor(self) -> bool:
        return self.bus.probe(self.address)

    def make_hello(self, now: float) -> Dict[str, Any]:
        return events.stamp(
            {"type": "hello", "device": self.name, "sensor_ok": self.sensor_ok},
            now,
        )

    def make_sample(self, now: float) -> Optional[Dict[str, Any]]:
        return events.stamp(
            {"type": "sample", "device": self.name, "sensor_ok": self.sensor_ok},
            now,
        )

    def poll(self, now: float) -> List[Dict[str, Any]]:
        return []
