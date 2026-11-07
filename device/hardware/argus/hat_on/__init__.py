"""VL53L0X hat-on ToF via the Adafruit CircuitPython library, continuous mode, polled at 10 Hz (spec §4.3)."""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Protocol

from argus.contracts import Health, ModuleContext, TofSample

from .._common import HealthTracker, StoppableThread, get_blinka_i2c

OUT_OF_RANGE_RAW = 8190  # sensor/library report >= 8190 mm when nothing is in range


class TofDevice(Protocol):
    def start(self, timing_budget_us: int) -> None: ...
    def range_mm(self) -> int: ...
    def stop(self) -> None: ...


class AdafruitVl53:
    def start(self, timing_budget_us: int) -> None:
        import adafruit_vl53l0x  # lazy

        self._s = adafruit_vl53l0x.VL53L0X(get_blinka_i2c())
        self._s.measurement_timing_budget = timing_budget_us
        self._s.start_continuous()

    def range_mm(self) -> int:
        return int(self._s.range)  # blocks <= one budget in continuous mode; RuntimeError on timeout

    def stop(self) -> None:
        self._s.stop_continuous()


class Vl53HatOn:
    def __init__(self, ctx: ModuleContext, device: TofDevice | None = None) -> None:
        o = ctx.options
        self._clock = ctx.clock
        self._dev = device or AdafruitVl53()
        self._budget_us = int(o.get("timing_budget_us", 33000))
        self._max_mm = float(o.get("max_range_mm", 1200))
        self._period_s = float(o.get("poll_interval_ms", 100)) / 1000.0
        self._q: deque[TofSample] = deque(maxlen=int(o.get("queue_max", 512)))
        self._lock = threading.Lock()
        self._health = HealthTracker(ctx.clock, stale_ms=float(o.get("stale_ms", 3000)))
        self._thread: StoppableThread | None = None

    def poll_once(self) -> TofSample:
        t = self._clock.now_ns()
        try:
            r = self._dev.range_mm()
        except Exception as e:
            self._health.error(f"VL53L0X error: {e}")
            s = TofSample(t, None, "error")
        else:
            if r >= OUT_OF_RANGE_RAW or r > self._max_mm or r <= 0:
                s = TofSample(t, None, "out_of_range")
            else:
                s = TofSample(t, float(r), "ok")
            self._health.sample(t)  # device responded (out_of_range is a valid reading)
        with self._lock:
            self._q.append(s)
        return s

    def _loop(self, stop: threading.Event) -> None:
        # Fixed rate (spec: 10 Hz): sleep only what is left of the period, because range_mm() itself
        # blocks for up to one measurement (~33 ms), so "read, then sleep a full period" would run slower.
        while not stop.is_set():
            t0 = time.monotonic()
            self.poll_once()
            stop.wait(max(0.0, self._period_s - (time.monotonic() - t0)))

    def start(self) -> None:
        if self._thread is not None:
            return
        self._health.mark_started()
        try:
            self._dev.start(self._budget_us)
        except Exception as e:
            self._health.error(f"VL53L0X init failed: {e}")
            return
        self._thread = StoppableThread("argus-hat_on", self._loop)
        self._thread.start()

    def stop(self) -> None:
        if self._thread:
            self._thread.stop()
            self._thread = None
            try:
                self._dev.stop()
            except Exception:
                pass

    def read(self) -> list[TofSample]:
        with self._lock:
            out = list(self._q)
            self._q.clear()
        return out

    def health(self) -> Health:
        return self._health.snapshot(running=self._thread is not None and self._thread.alive)


def create(ctx: ModuleContext) -> Vl53HatOn:
    return Vl53HatOn(ctx)
