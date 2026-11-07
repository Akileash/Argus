"""HC-SR04 sequential sweep, MVP = 2 rear sensors R2,R3 (spec §3.2a); pins for R1/R4/TOP stay defined for later.
Echo width via lgpio edge alerts (no busy-wait).

One sensor at a time, `slot_ms` (30) per sensor, 10 us trigger: 60 ms per sweep, ~16.7 Hz per sensor with the pair.
distance_mm = width_us * speed_of_sound(m/s) / 2 / 1000 ; speed = 343 m/s, or 331.3 + 0.606*T if `temp_c` set.
"""

from __future__ import annotations

import threading
import time

from argus.contracts import DEFAULT_ACTIVE_SENSORS, Health, ModuleContext, RangeSample

from .._common import Gpio, HealthTracker, LgpioGpio, StoppableThread

DEFAULT_PINS = {  # (trig, echo) BCM, spec §2.3
    "R1": (5, 6),
    "R2": (19, 26),
    "R3": (23, 24),
    "R4": (16, 25),
    "TOP": (20, 21),
}
DEFAULT_ORDER = list(DEFAULT_ACTIVE_SENSORS)


def width_to_mm(width_us: float, temp_c: float | None = None) -> float:
    speed = 343.0 if temp_c is None else 331.3 + 0.606 * temp_c  # m/s
    return width_us * speed / 2000.0  # us * m/s = micro-m ; /2 round trip ; /1000 -> mm


class Hcsr04Array:
    def __init__(self, ctx: ModuleContext, gpio: Gpio | None = None) -> None:
        o = ctx.options
        self._clock = ctx.clock
        self._gpio = gpio
        self._own_gpio = gpio is None
        self._slot_s = float(o.get("slot_ms", 30)) / 1000.0
        self._trig_us = int(o.get("trigger_us", 10))
        self._max_mm = float(o.get("max_range_mm", 4000))
        self._min_mm = float(o.get("min_range_mm", 20))
        self._temp_c = o.get("temp_c")
        self._chip = int(o.get("gpiochip", 0))
        pins = {k: tuple(v) for k, v in {**DEFAULT_PINS, **(o.get("pins") or {})}.items()}
        self._order = list(o.get("order", DEFAULT_ORDER))
        self.sensors = tuple(self._order)
        for sid in self._order:
            if sid not in pins:
                raise ValueError(f"unknown sensor {sid}")
        self._pins = pins
        # wait for echo: slightly less than the slot so the slot never overruns
        self._echo_timeout_s = float(o.get("echo_timeout_ms", self._slot_s * 1000 - 3)) / 1000.0
        self._q: list[RangeSample] = []
        self._qlock = threading.Lock()
        self._health = HealthTracker(ctx.clock, stale_ms=float(o.get("stale_ms", 500)))
        self._thread: StoppableThread | None = None
        # echo state (single active sensor at a time)
        self._active_echo: int | None = None
        self._rise: int | None = None
        self._width_us: float | None = None
        self._done = threading.Event()

    def set_temperature(self, temp_c: float | None) -> None:
        """Runtime speed-of-sound compensation (e.g. from the env module)."""
        self._temp_c = temp_c

    # lgpio callback: (level, timestamp_ns)
    def _on_edge(self, pin: int, level: int, ts_ns: int) -> None:
        if pin != self._active_echo:
            return
        if level == 1:
            self._rise = ts_ns
        elif level == 0 and self._rise is not None and self._width_us is None:
            self._width_us = (ts_ns - self._rise) / 1000.0
            self._done.set()

    def measure(self, sid: str) -> RangeSample:
        trig, echo = self._pins[sid]
        self._rise = None
        self._width_us = None
        self._done.clear()
        self._active_echo = echo
        t_ns = self._clock.now_ns()
        self._gpio.pulse(trig, self._trig_us)
        got = self._done.wait(self._echo_timeout_s)
        self._active_echo = None
        mm = None
        if got and self._width_us is not None:
            d = width_to_mm(self._width_us, self._temp_c)
            if self._min_mm <= d <= self._max_mm:
                mm = round(d, 1)
        return RangeSample(t_ns, sid, mm)  # type: ignore[arg-type]

    def sweep_once(self) -> list[RangeSample]:
        out = []
        for sid in self._order:
            t0 = time.monotonic()
            try:
                out.append(self.measure(sid))
                self._health.sample()
            except Exception as e:
                self._health.error(f"range {sid} error: {e}")
            rem = self._slot_s - (time.monotonic() - t0)
            if rem > 0:
                ev = self._thread.stop_event if self._thread else None
                ev.wait(rem) if ev else time.sleep(rem)
        with self._qlock:
            self._q.extend(out)
        return out

    def _loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self.sweep_once()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._health.mark_started()
        try:
            if self._gpio is None:
                self._gpio = LgpioGpio(self._chip)
            for sid in self._order:
                trig, echo = self._pins[sid]
                self._gpio.claim_output(trig, 0)
                self._gpio.claim_alert(echo, lambda lvl, ts, p=echo: self._on_edge(p, lvl, ts))
        except Exception as e:
            self._health.error(f"range GPIO init failed: {e}")
            return
        self._thread = StoppableThread("argus-range", self._loop)
        self._thread.start()

    def stop(self) -> None:
        if self._thread:
            self._thread.stop()
            self._thread = None
        if self._gpio is not None and self._own_gpio:
            try:
                self._gpio.close()
            except Exception:
                pass
            self._gpio = None

    def read(self) -> list[RangeSample]:
        with self._qlock:
            out, self._q = self._q, []
        return out

    def health(self) -> Health:
        return self._health.snapshot(running=self._thread is not None and self._thread.alive)


def create(ctx: ModuleContext) -> Hcsr04Array:
    return Hcsr04Array(ctx)
