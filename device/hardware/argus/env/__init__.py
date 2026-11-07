"""SCD41 via adafruit_scd4x, periodic measurement (5 s, ~0.2 Hz). Context only."""

from __future__ import annotations

import threading
from collections import deque
from typing import Protocol

from argus.contracts import EnvSample, Health, ModuleContext

from .._common import HealthTracker, StoppableThread, get_blinka_i2c


class Scd4Device(Protocol):
    def start(self, temperature_offset: float | None) -> None: ...
    def data_ready(self) -> bool: ...
    def values(self) -> tuple[float, float, float]: ...  # co2, temp_c, rh
    def stop(self) -> None: ...


class AdafruitScd4:
    def start(self, temperature_offset: float | None) -> None:
        import adafruit_scd4x  # lazy

        self._s = adafruit_scd4x.SCD4X(get_blinka_i2c())
        try:
            self._s.stop_periodic_measurement()  # config setters require idle state
        except Exception:
            pass
        if temperature_offset is not None:
            self._s.temperature_offset = temperature_offset
        self._s.start_periodic_measurement()

    def data_ready(self) -> bool:
        return bool(self._s.data_ready)

    def values(self):
        return float(self._s.CO2), float(self._s.temperature), float(self._s.relative_humidity)

    def stop(self) -> None:
        self._s.stop_periodic_measurement()


class Scd41Env:
    def __init__(self, ctx: ModuleContext, device: Scd4Device | None = None) -> None:
        o = ctx.options
        self._clock = ctx.clock
        self._dev = device or AdafruitScd4()
        self._offset = o.get("temperature_offset_c")
        self._poll_s = float(o.get("poll_interval_s", 1.0))
        self._q: deque[EnvSample] = deque(maxlen=int(o.get("queue_max", 64)))
        self._lock = threading.Lock()
        self._health = HealthTracker(ctx.clock, stale_ms=float(o.get("stale_ms", 30000)))
        self._thread: StoppableThread | None = None

    def poll_once(self) -> EnvSample | None:
        try:
            if not self._dev.data_ready():
                return None
            co2, t, rh = self._dev.values()
        except Exception as e:
            self._health.error(f"SCD41 error: {e}")
            return None
        s = EnvSample(self._clock.now_ns(), co2, t, rh)
        with self._lock:
            self._q.append(s)
        self._health.sample(s.t_ns)
        return s

    def _loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self.poll_once()
            stop.wait(self._poll_s)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._health.mark_started()
        try:
            self._dev.start(self._offset)
        except Exception as e:
            self._health.error(f"SCD41 init failed: {e}")
            return
        self._thread = StoppableThread("argus-env", self._loop)
        self._thread.start()

    def stop(self) -> None:
        if self._thread:
            self._thread.stop()
            self._thread = None
            try:
                self._dev.stop()
            except Exception:
                pass

    def read(self) -> list[EnvSample]:
        with self._lock:
            out = list(self._q)
            self._q.clear()
        return out

    def health(self) -> Health:
        return self._health.snapshot(running=self._thread is not None and self._thread.alive)


def create(ctx: ModuleContext) -> Scd41Env:
    return Scd41Env(ctx)
