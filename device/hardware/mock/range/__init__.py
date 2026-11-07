"""Mock HC-SR04 array: interleaved sweep, one sensor every 30 ms, 150 ms per sweep (spec §2.3, §5.2)."""

from __future__ import annotations

import numpy as np

from argus.contracts import SENSOR_IDS, ModuleContext, RangeArray, RangeSample
from hardware.mock._base import MockStream
from hardware.mock.scenario import SENSOR_SLOT_S, ScenarioRun


class MockRange(MockStream):
    stream = "range"
    period_s = SENSOR_SLOT_S

    def _make(self, run: ScenarioRun, idx: np.ndarray) -> list[RangeSample]:
        out = []
        for n in idx:
            t = float(n) * self.period_s
            sid = SENSOR_IDS[int(n) % len(SENSOR_IDS)]
            out.append(RangeSample(self._t_ns(run, t), sid, run.scenario.range_mm(t, sid, int(n))))
        return out


def create(ctx: ModuleContext) -> RangeArray:
    return MockRange(ctx)
