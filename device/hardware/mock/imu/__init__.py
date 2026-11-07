"""Mock IMU: scenario-driven MPU-6050 stand-in (spec §4.2, §5.2). 400 Hz, ±16 g, ±1000 deg/s."""

from __future__ import annotations

import numpy as np

from argus.contracts import Imu, ImuSample, ModuleContext
from hardware.mock._base import MockStream
from hardware.mock.scenario import ScenarioRun


class MockImu(MockStream):
    stream = "imu"

    def __init__(self, ctx: ModuleContext) -> None:
        super().__init__(ctx)
        self.period_s = 1.0 / float(ctx.options.get("rate_hz", 400))

    def _make(self, run: ScenarioRun, idx: np.ndarray) -> list[ImuSample]:
        t = idx * self.period_s
        rows = run.scenario.imu(t, idx)
        t0 = run.t0_ns
        return [
            ImuSample(t0 + round(ts * 1e9), *map(float, r)) for ts, r in zip(t, rows, strict=True)
        ]


def create(ctx: ModuleContext) -> Imu:
    return MockImu(ctx)
