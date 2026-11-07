"""Optional boot-status LED (spec §4.3: solid while calibrating, blinking = "put the hat on").

Driver-independent: :class:`StatusLed` only needs a ``write(level)`` callable, so tests and other
boards can pass anything. :func:`open_led` builds one on a Pi GPIO via lgpio (imported lazily); it
returns ``None`` (and logs) if that is not possible, because a missing LED must never stop the hat.
Enabled by ``device.status_led_pin`` in config.yaml (17 on the real hat); unset = no LED.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)

MODES = ("off", "solid", "blink")


class StatusLed:
    def __init__(self, write: Callable[[int], None], blink_hz: float = 2.0, close: Callable[[], None] | None = None):
        self._write = write
        self._half_s = 0.5 / blink_hz
        self._close = close
        self._mode = "off"
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="argus-status-led", daemon=True)
        self._thread.start()

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"LED mode must be one of {MODES}")
        self._mode = mode
        self._wake.set()

    def _safe(self, level: int) -> None:
        try:
            self._write(level)
        except Exception:
            log.exception("status LED write failed")

    def _loop(self) -> None:
        level = 0
        while not self._stop.is_set():
            self._wake.clear()
            mode = self._mode
            if mode == "blink":
                level ^= 1
                self._safe(level)
                self._wake.wait(self._half_s)
                continue
            level = 1 if mode == "solid" else 0
            self._safe(level)
            self._wake.wait()  # until the mode changes

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=1.0)
        self._safe(0)
        if self._close is not None:
            try:
                self._close()
            except Exception:
                log.exception("status LED close failed")


def open_led(pin: int | None, chip: int = 0) -> StatusLed | None:
    if pin is None:
        return None
    try:
        import lgpio  # lazy: Pi only

        h = lgpio.gpiochip_open(chip)
        lgpio.gpio_claim_output(h, pin, 0)
    except Exception as e:  # noqa: BLE001
        log.warning("status LED on GPIO%s unavailable (%s); continuing without it", pin, e)
        return None
    return StatusLed(lambda level: lgpio.gpio_write(h, pin, level), close=lambda: lgpio.gpiochip_close(h))
