"""Mock SCD41 air-quality sensor, 0.2 Hz, context only (spec §5.2)."""

from __future__ import annotations

import numpy as np

from argus.contracts import EnvSample, EnvSensor, ModuleContext
from hardware.mock._base import MockStream
from hardware.mock.scenario import ScenarioRun


class MockEnv(MockStream):
    stream = "env"
    period_s = 5.0

    def _make(self, run: ScenarioRun, idx: np.ndarray) -> list[EnvSample]:
        out = []
        for n in idx:
            t = float(n) * self.period_s
            co2, temp, rh = run.scenario.env(t, int(n))
            out.append(EnvSample(self._t_ns(run, t), co2, temp, rh))
        return out


def create(ctx: ModuleContext) -> EnvSensor:
    return MockEnv(ctx)
