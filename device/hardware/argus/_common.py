"""Shared helpers: health bookkeeping and lazily-imported hardware access layers.

Every driver talks to hardware only through the thin protocols below (`I2CBus`, `Gpio`) so tests
can inject fakes. The real implementations import smbus2 / lgpio inside their constructors.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Protocol

from argus.contracts import Clock, Health

log = logging.getLogger("argus.hw")


class HealthTracker:
    """Thread-safe error counter + last-sample age -> contract `Health`."""

    def __init__(self, clock: Clock, stale_ms: float) -> None:
        self._clock = clock
        self._stale_ms = stale_ms
        self._lock = threading.Lock()
        self._errors = 0
        self._note = ""
        self._last_sample_ns: int | None = None
        self._started_ns: int | None = None

    def mark_started(self) -> None:
        with self._lock:
            self._started_ns = self._clock.now_ns()
            self._last_sample_ns = None
            self._note = ""

    def sample(self, t_ns: int | None = None) -> None:
        with self._lock:
            self._last_sample_ns = self._clock.now_ns() if t_ns is None else t_ns

    def error(self, note: str) -> None:
        with self._lock:
            self._errors += 1
            self._note = note
        log.warning("%s", note)

    @property
    def error_count(self) -> int:
        with self._lock:
            return self._errors

    def snapshot(self, running: bool, healthy: bool = True) -> Health:
        now = self._clock.now_ns()
        with self._lock:
            age = None if self._last_sample_ns is None else (now - self._last_sample_ns) / 1e6
            if not running:
                ok = False
            elif age is None:
                # grace period after start before the first sample
                ok = self._started_ns is not None and (now - self._started_ns) / 1e6 < 2 * self._stale_ms
            else:
                ok = age <= self._stale_ms
            return Health(ok=ok and healthy, last_sample_age_ms=age, error_count=self._errors, note=self._note)


class StoppableThread:
    """Small wrapper: run `target(stop_event)` in a daemon thread."""

    def __init__(self, name: str, target: Callable[[threading.Event], None]) -> None:
        self.stop_event = threading.Event()
        self._target = target
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)

    def _run(self) -> None:
        try:
            self._target(self.stop_event)
        except Exception:  # pragma: no cover - last-resort guard
            log.exception("driver thread crashed")

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self.stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout)

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()


# ------------------------------------------------------------------ I2C layer
class I2CBus(Protocol):
    def write_byte(self, addr: int, reg: int, value: int) -> None: ...
    def read_byte(self, addr: int, reg: int) -> int: ...
    def read_block(self, addr: int, reg: int, length: int) -> bytes: ...
    def close(self) -> None: ...


class SmbusI2C:
    """smbus2-backed bus. Imports smbus2 lazily (Pi `[pi]` extra)."""

    def __init__(self, bus: int = 1) -> None:
        from smbus2 import SMBus, i2c_msg  # lazy

        self._msg = i2c_msg
        self._bus = SMBus(bus)

    def write_byte(self, addr: int, reg: int, value: int) -> None:
        self._bus.write_byte_data(addr, reg, value & 0xFF)

    def read_byte(self, addr: int, reg: int) -> int:
        return self._bus.read_byte_data(addr, reg)

    def read_block(self, addr: int, reg: int, length: int) -> bytes:
        # i2c_rdwr allows reads > 32 bytes (SMBus block reads do not).
        w = self._msg.write(addr, [reg])
        r = self._msg.read(addr, length)
        self._bus.i2c_rdwr(w, r)
        return bytes(r)

    def close(self) -> None:
        self._bus.close()


_blinka_lock = threading.Lock()
_blinka_i2c = None


def get_blinka_i2c():
    """Shared busio.I2C for Adafruit CircuitPython drivers (lazy; Pi only)."""
    global _blinka_i2c
    with _blinka_lock:
        if _blinka_i2c is None:
            import board  # lazy
            import busio  # lazy

            _blinka_i2c = busio.I2C(board.SCL, board.SDA)
        return _blinka_i2c


# ------------------------------------------------------------------ GPIO layer
class Gpio(Protocol):
    def claim_output(self, pin: int, initial: int = 0) -> None: ...
    def claim_alert(self, pin: int, callback: Callable[[int, int], None]) -> None:
        """Both-edge alerts; callback(level, timestamp_ns). Timestamps only need to be self-consistent."""
        ...

    def write(self, pin: int, level: int) -> None: ...
    def pulse(self, pin: int, width_us: int) -> None: ...
    def set_pwm(self, pin: int, freq_hz: float, duty_pct: float) -> None: ...
    def close(self) -> None: ...


class LgpioGpio:
    """lgpio-backed GPIO (Pi 4 -> gpiochip0). Imports lgpio lazily."""

    def __init__(self, chip: int = 0) -> None:
        import lgpio  # lazy

        self._lg = lgpio
        self._h = lgpio.gpiochip_open(chip)
        self._cbs: list = []

    def claim_output(self, pin: int, initial: int = 0) -> None:
        self._lg.gpio_claim_output(self._h, pin, initial)

    def claim_alert(self, pin: int, callback: Callable[[int, int], None]) -> None:
        lg = self._lg
        lg.gpio_claim_alert(self._h, pin, lg.BOTH_EDGES, lg.SET_PULL_DOWN)
        # lgpio callback signature: (chip, gpio, level, timestamp_ns)
        self._cbs.append(lg.callback(self._h, pin, lg.BOTH_EDGES, lambda c, g, lvl, ts: callback(lvl, ts)))

    def write(self, pin: int, level: int) -> None:
        self._lg.gpio_write(self._h, pin, level)

    def pulse(self, pin: int, width_us: int) -> None:
        # tx_pulse(handle, gpio, pulse_on_us, pulse_off_us, offset_us, cycles): one timed pulse.
        self._lg.tx_pulse(self._h, pin, width_us, 100, 0, 1)

    def set_pwm(self, pin: int, freq_hz: float, duty_pct: float) -> None:
        # tx_pwm(handle, gpio, freq_hz, duty_percent, offset, cycles=0 -> forever)
        self._lg.tx_pwm(self._h, pin, freq_hz, duty_pct, 0, 0)

    def close(self) -> None:
        for cb in self._cbs:
            try:
                cb.cancel()
            except Exception:
                pass
        self._lg.gpiochip_close(self._h)
