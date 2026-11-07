"""Mock VL53L0X crown sensor, 10 Hz (spec §4.3, §5.2)."""

from __future__ import annotations

import numpy as np

from argus.contracts import HatOnSensor, ModuleContext, TofSample
from hardware.mock._base import MockStream
from hardware.mock.scenario import ScenarioRun


class MockHatOn(MockStream):
    stream = "tof"
    period_s = 0.1

    def _make(self, run: ScenarioRun, idx: np.ndarray) -> list[TofSample]:
        out = []
        for n in idx:
            t = float(n) * self.period_s
            rng, status = run.scenario.tof(t, int(n))
            out.append(TofSample(self._t_ns(run, t), rng, status))  # type: ignore[arg-type]
        return out


def create(ctx: ModuleContext) -> HatOnSensor:
    return MockHatOn(ctx)
