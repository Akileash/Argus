"""Shared machinery for scenario-driven mock streaming modules (spec §5.3 ``mock``).

Mocks have no threads: ``read()`` generates every sample whose scenario time has elapsed on the
injected clock since the previous call. With a ``VirtualClock`` tests advance time instantly; with
the real clock the mock behaves like a live sensor. Fault injection (``kill``/``stall``) lets the
supervisor's fallback logic be tested.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np

from argus.contracts import Health, ModuleContext
from hardware.mock.scenario import ScenarioRun, get_run

log = logging.getLogger(__name__)


class MockStream:
    """Base for imu/range/hat_on/env mocks. Subclasses set ``period_s`` and implement ``_make``."""

    period_s: float = 1.0
    stream: str = ""

    def __init__(self, ctx: ModuleContext) -> None:
        self.ctx = ctx
        self.clock = ctx.clock
        self._running = False
        self._dead = False
        self._stalled = False
        self._run: ScenarioRun | None = None
        self._next_idx = 0
        self._last_t_ns: int | None = None
        self._errors = 0

    # -- lifecycle
    def start(self) -> None:
        if self._running:
            return
        self._run = get_run(self.ctx.scenario or self.ctx.options.get("scenario"), self.clock)
        self._next_idx = self._first_idx_after_now()
        self._running = True

    def stop(self) -> None:
        self._running = False

    # -- fault injection (tests / chaos drills)
    def kill(self) -> None:
        """Hard failure: no samples and health().ok is False."""
        self._dead = True

    def stall(self) -> None:
        """Silent hang: no samples but health() still claims ok (supervisor must notice staleness)."""
        self._stalled = True

    def revive(self) -> None:
        self._dead = self._stalled = False
        if self._running:
            self._next_idx = self._first_idx_after_now()

    # -- contract
    def read(self) -> list[Any]:
        try:
            if not self._running or self._run is None:
                return []
            now_s = self._run.t_s(self.clock.now_ns())
            if self._dead or self._stalled:
                self._next_idx = max(self._next_idx, self._first_idx_after(now_s))  # samples are lost
                return []
            end = math.floor(now_s / self.period_s + 1e-9) + 1
            if end <= self._next_idx:
                return []
            idx = np.arange(self._next_idx, end)
            self._next_idx = end
            out = self._make(self._run, idx)
            if out:
                self._last_t_ns = out[-1].t_ns
            return out
        except Exception:  # contract: never raise out of read()
            self._errors += 1
            log.exception("mock %s read failed", self.stream)
            return []

    def health(self) -> Health:
        age = None if self._last_t_ns is None else (self.clock.now_ns() - self._last_t_ns) / 1e6
        if self._dead:
            return Health(False, age, self._errors, "killed (fault injection)")
        return Health(self._running, age, self._errors, "" if self._running else "not started")

    # -- helpers
    def _first_idx_after(self, t_s: float) -> int:
        return math.ceil(t_s / self.period_s - 1e-9)

    def _first_idx_after_now(self) -> int:
        assert self._run is not None
        return max(0, self._first_idx_after(self._run.t_s(self.clock.now_ns())))

    def _make(self, run: ScenarioRun, idx: np.ndarray) -> list[Any]:  # pragma: no cover - abstract
        raise NotImplementedError

    @staticmethod
    def _t_ns(run: ScenarioRun, t_s: float) -> int:
        return run.t0_ns + round(t_s * 1e9)
