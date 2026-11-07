"""Mock haptic driver: records every call so L3 pattern tests can assert on them (spec §5.2)."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from argus.contracts import HapticDriver, Health, ModuleContext

log = logging.getLogger(__name__)
MOTORS = ("top", "back")


@dataclass(frozen=True, slots=True)
class HapticCall:
    t_ns: int
    motor: str
    intensity: float


class MockHaptics:
    def __init__(self, ctx: ModuleContext) -> None:
        self.clock = ctx.clock
        self.calls: list[HapticCall] = []
        self.state: dict[str, float] = dict.fromkeys(MOTORS, 0.0)
        self._lock = threading.Lock()
        self._running = False
        self._dead = False
        self._errors = 0

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self.off_all()
        self._running = False

    def kill(self) -> None:
        """Simulate a crashed driver: ``set`` raises and health() is not ok."""
        self._dead = True

    def revive(self) -> None:
        self._dead = False

    def set(self, motor: str, intensity: float) -> None:
        if self._dead:
            raise RuntimeError("mock haptics killed (fault injection)")
        if motor not in MOTORS:
            self._errors += 1
            log.error("mock haptics: unknown motor %r", motor)
            return
        level = min(1.0, max(0.0, float(intensity)))
        with self._lock:
            self.state[motor] = level
            self.calls.append(HapticCall(self.clock.now_ns(), motor, level))

    def off_all(self) -> None:
        for m in MOTORS:
            if not self._dead:
                self.set(m, 0.0)

    def health(self) -> Health:
        if self._dead:
            return Health(False, None, self._errors, "killed (fault injection)")
        return Health(self._running, None, self._errors, "" if self._running else "not started")


def create(ctx: ModuleContext) -> HapticDriver:
    return MockHaptics(ctx)
