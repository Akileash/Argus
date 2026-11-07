"""Team haptics adapter skeleton (spec §2.1, §2.4, §5.5). Not pasted yet: ``create`` raises.

Contract: ``set(motor, intensity)`` with motor in {"top", "back"}, intensity 0.0-1.0; ``off_all()``.
Patterns (pulse rates, the 1.5 s impact buzz) live in L3, NOT here: the driver only sets a strength.

  (a) ``TeamHaptics``        import their code (e.g. ``set_duty(pin, 0..100)``) and call it directly, or
  (b) ``TeamHapticsSerial``  the Arduino fallback (§2.4): write ``H,<top|back|both>,<id>\n`` lines to
      a serial port, or run their script as a subprocess and write commands to its stdin.
Run: ``pytest tests/contract --module haptics --impl team`` and ``argus-cli buzz both``.
"""

from __future__ import annotations

import logging

from argus.contracts import HapticDriver, Health, ModuleContext

log = logging.getLogger(__name__)
MOTORS = ("top", "back")


class TeamHaptics:
    """Pattern (a): direct calls into their code. Must never raise out of set()/off_all()."""

    def __init__(self, ctx: ModuleContext) -> None:
        self.ctx = ctx
        self._errors = 0
        self._ok = False

    def start(self) -> None:
        # from hardware.team.haptics import their_script as their_module   # <- their code, unchanged
        # their_module.setup()
        self._ok = True

    def stop(self) -> None:
        self.off_all()
        self._ok = False

    def set(self, motor: str, intensity: float) -> None:
        try:
            # level = min(1.0, max(0.0, float(intensity)))
            # their_module.set_duty(PIN[motor], level * 100)   # convert to THEIR scale
            raise NotImplementedError
        except Exception:
            self._errors += 1
            log.exception("team haptics set(%s, %s) failed", motor, intensity)

    def off_all(self) -> None:
        for m in MOTORS:
            self.set(m, 0.0)

    def health(self) -> Health:
        return Health(self._ok and self._errors == 0, None, self._errors, "")


class TeamHapticsSerial(TeamHaptics):
    """Pattern (b): Arduino over USB serial (spec §2.4 fallback A). Sketch only."""

    def set(self, motor: str, intensity: float) -> None:
        # import serial  (pyserial) lazily here; port from ctx.options["port"], 115200 baud
        # self._port.write(f"H,{motor},{int(min(1, max(0, intensity)) * 255)}\n".encode())
        super().set(motor, intensity)


def create(ctx: ModuleContext) -> HapticDriver:
    raise NotImplementedError("team impl for haptics not pasted yet — see README")
    # return TeamHaptics(ctx)         # pattern (a)
    # return TeamHapticsSerial(ctx)   # pattern (b)
