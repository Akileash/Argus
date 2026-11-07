"""Mock HC-SR04 array: one active sensor every 30 ms, so 60 ms per sweep with the MVP pair (spec §3.2a)."""

from __future__ import annotations

import numpy as np

from argus.contracts import DEFAULT_ACTIVE_SENSORS, ModuleContext, RangeArray, RangeSample
from hardware.mock._base import MockStream
from hardware.mock.scenario import SENSOR_SLOT_S, ScenarioRun


class MockRange(MockStream):
    stream = "range"
    period_s = SENSOR_SLOT_S

    def __init__(self, ctx: ModuleContext) -> None:
        super().__init__(ctx)
        self.sensors = tuple(ctx.options.get("order", DEFAULT_ACTIVE_SENSORS))

    def _make(self, run: ScenarioRun, idx: np.ndarray) -> list[RangeSample]:
        out = []
        for n in idx:
            t = float(n) * self.period_s
            sid = self.sensors[int(n) % len(self.sensors)]
            out.append(RangeSample(self._t_ns(run, t), sid, run.scenario.range_mm(t, sid, int(n))))
        return out


def create(ctx: ModuleContext) -> RangeArray:
    return MockRange(ctx)
