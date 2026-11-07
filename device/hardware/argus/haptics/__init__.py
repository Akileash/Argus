"""L293D haptics: EN pins PWM'd via lgpio tx_pwm. Top = GPIO12, back = GPIO13, 1 kHz default.

Honest health: the L293D gives no current feedback, so health is "GPIO claimed and the last write
succeeded" only. It cannot detect a disconnected or stalled motor.
"""

from __future__ import annotations

import math
import threading

from argus.contracts import Health, ModuleContext, Motor

from .._common import Gpio, HealthTracker, LgpioGpio

DEFAULT_PINS: dict[str, int] = {"top": 12, "back": 13}


class L293dHaptics:
    def __init__(self, ctx: ModuleContext, gpio: Gpio | None = None) -> None:
        o = ctx.options
        self._gpio = gpio
        self._own_gpio = gpio is None
        self._pins = {**DEFAULT_PINS, **(o.get("pins") or {})}
        self._freq = float(o.get("pwm_hz", 1000))
        self._min_duty = float(o.get("min_duty", 0.0))  # fraction; raise if motors stall at low duty
        self._max_duty = float(o.get("max_duty", 1.0))
        self._chip = int(o.get("gpiochip", 0))
        self._lock = threading.Lock()
        self._claimed = False
        self._last_ok = True
        self._health = HealthTracker(ctx.clock, stale_ms=1e12)
        self.duty_pct: dict[str, float] = {"top": 0.0, "back": 0.0}

    def duty_for(self, intensity: float) -> float:
        """intensity 0..1 -> duty percent. 0 -> exactly 0; >0 -> min_duty..max_duty."""
        if intensity is None or (isinstance(intensity, float) and math.isnan(intensity)):
            return 0.0
        i = max(0.0, min(1.0, float(intensity)))
        if i == 0.0:
            return 0.0
        return 100.0 * (self._min_duty + (self._max_duty - self._min_duty) * i)

    def start(self) -> None:
        self._health.mark_started()
        try:
            if self._gpio is None:
                self._gpio = LgpioGpio(self._chip)
            for pin in self._pins.values():
                self._gpio.claim_output(pin, 0)
            self._claimed = True
            self._last_ok = True
        except Exception as e:
            self._claimed = False
            self._health.error(f"haptics GPIO claim failed: {e}")

    def stop(self) -> None:
        self.off_all()
        if self._gpio is not None and self._own_gpio:
            try:
                self._gpio.close()
            except Exception:
                pass
            self._gpio = None
        self._claimed = False

    def set(self, motor: Motor, intensity: float) -> None:
        try:
            pin = self._pins[motor]
            duty = self.duty_for(intensity)
            with self._lock:
                self._gpio.set_pwm(pin, self._freq, duty)
                self.duty_pct[motor] = duty
            self._last_ok = True
        except Exception as e:
            self._last_ok = False
            self._health.error(f"haptics set({motor!r}, {intensity!r}) failed: {e}")

    def off_all(self) -> None:
        for m in self._pins:
            self.set(m, 0.0)  # type: ignore[arg-type]

    def health(self) -> Health:
        h = self._health.snapshot(running=True)
        h.ok = self._claimed and self._last_ok
        h.last_sample_age_ms = None
        h.note = h.note or "no motor current sensing: ok means GPIO claimed and last write succeeded"
        return h


def create(ctx: ModuleContext) -> L293dHaptics:
    return L293dHaptics(ctx)
