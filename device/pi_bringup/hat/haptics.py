"""Dual DRV2605L haptics (ERM disc motors) over I2C.

LEFT  → /dev/i2c-1 addr 0x5A
RIGHT → /dev/i2c-3 addr 0x5A  (dtoverlay=i2c3,pins_4_5)

warn(side) fires the effect immediately on the calling thread, then a
worker finishes the remaining pulses so sensor loops stay free.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Callable, Dict, List, Optional

from smbus2 import SMBus

from hat import config as cfg

# DRV2605L registers
_REG_MODE = 0x01
_REG_LIBRARY = 0x03
_REG_WAVESEQ1 = 0x04
_REG_GO = 0x0C
_REG_FEEDBACK = 0x1A
_MODE_INTTRIG = 0x00
_MODE_STANDBY = 0x40


class DRV2605L:
    """Minimal ERM driver via smbus2 (no Blinka required)."""

    def __init__(self, bus_id: int, addr: int = cfg.DRV_ADDR, name: str = ""):
        self.name = name
        self.addr = addr
        self.bus = SMBus(bus_id)
        self._setup()

    def _w(self, reg: int, val: int) -> None:
        self.bus.write_byte_data(self.addr, reg, val & 0xFF)

    def _r(self, reg: int) -> int:
        return self.bus.read_byte_data(self.addr, reg)

    def _setup(self) -> None:
        # Out of standby
        self._w(_REG_MODE, _MODE_INTTRIG)
        # ERM mode: clear N_ERM_LRA (bit 7) in feedback
        fb = self._r(_REG_FEEDBACK)
        self._w(_REG_FEEDBACK, fb & 0x7F)
        # Library 1 = ERM
        self._w(_REG_LIBRARY, 0x01)

    def play_effect(self, effect_id: int) -> None:
        """Queue one ROM effect and GO (non-blocking at chip; effect runs itself)."""
        self._w(_REG_MODE, _MODE_INTTRIG)
        self._w(_REG_WAVESEQ1, effect_id & 0x7F)
        self._w(_REG_WAVESEQ1 + 1, 0x00)  # end sequence
        self._w(_REG_GO, 0x01)

    def stop(self) -> None:
        self._w(_REG_GO, 0x00)
        self._w(_REG_MODE, _MODE_STANDBY)
        self._w(_REG_MODE, _MODE_INTTRIG)

    def close(self) -> None:
        try:
            self.stop()
        except OSError:
            pass
        try:
            self.bus.close()
        except OSError:
            pass


class Haptics:
    """
    LEFT / RIGHT / both DRV2605L buzzers.
    Instant first effect on warn(); remaining pulses via worker thread.
    """

    def __init__(
        self,
        dry_run: bool = False,
        on_buzz: Optional[Callable[[], None]] = None,
    ):
        self.dry_run = dry_run
        self.on_buzz = on_buzz
        self._q: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._devs: Dict[str, DRV2605L] = {}
        # Read-only for telemetry: perf_counter of the last effect fired per side (never read here).
        self.last_fire: Dict[str, float] = {"left": 0.0, "right": 0.0}

        if not dry_run:
            self._open_devices()

        self._worker = threading.Thread(target=self._run, name="haptics", daemon=True)
        self._worker.start()

    def _open_devices(self) -> None:
        try:
            self._devs["left"] = DRV2605L(
                cfg.I2C_BUS_HAPTIC_LEFT, cfg.DRV_ADDR, name="left"
            )
            print(f"[haptics] LEFT DRV2605L on i2c-{cfg.I2C_BUS_HAPTIC_LEFT} @ 0x{cfg.DRV_ADDR:02X}")
        except OSError as e:
            print(f"[haptics] LEFT DRV2605L FAILED: {e}")
            if cfg.HAPTIC_REQUIRE_RIGHT:
                raise

        try:
            self._devs["right"] = DRV2605L(
                cfg.I2C_BUS_HAPTIC_RIGHT, cfg.DRV_ADDR, name="right"
            )
            print(f"[haptics] RIGHT DRV2605L on i2c-{cfg.I2C_BUS_HAPTIC_RIGHT} @ 0x{cfg.DRV_ADDR:02X}")
        except OSError as e:
            msg = (
                f"[haptics] RIGHT DRV2605L FAILED: {e}\n"
                f"  Enable bus 3: add  dtoverlay=i2c3,pins_4_5  to "
                f"/boot/firmware/config.txt then reboot.\n"
                f"  Expect: i2cdetect -y 3  shows 5a"
            )
            print(msg)
            if cfg.HAPTIC_REQUIRE_RIGHT:
                raise

    def _sides(self, side: str) -> List[str]:
        if side == "both":
            return [s for s in ("left", "right") if s in self._devs]
        if side in ("left", "right"):
            return [side] if side in self._devs else []
        raise ValueError(f"unknown side: {side}")

    def _fire(self, side: str, effect_id: int) -> None:
        if self.on_buzz:
            self.on_buzz()
        now = time.perf_counter()
        if side in ("left", "both"):
            self.last_fire["left"] = now
        if side in ("right", "both"):
            self.last_fire["right"] = now
        if self.dry_run:
            print(f"[haptics dry-run] {side} effect={effect_id}")
            return
        for s in self._sides(side):
            try:
                self._devs[s].play_effect(effect_id)
            except OSError as e:
                print(f"[haptics] {s} play error: {e}")

    def _halt(self, side: str) -> None:
        if self.dry_run:
            return
        for s in self._sides(side):
            try:
                self._devs[s].stop()
            except OSError:
                pass

    def pulse(self, side: str, on_s: float, power: float = 1.0) -> None:
        # power unused on DRV ROM effects; kept for API compat
        self._q.put(("pulse", side, on_s, cfg.HAPTIC_EFFECT_ID))

    def pattern(
        self,
        side: str,
        pulses: int,
        on_s: float,
        off_s: float,
        power: float = 1.0,
    ) -> None:
        self._q.put(("pattern", side, pulses, on_s, off_s, cfg.HAPTIC_EFFECT_ID))

    def warn(self, side: str) -> None:
        """Fire effect NOW, then finish remaining pulses in the worker."""
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass

        effect = cfg.HAPTIC_EFFECT_ID
        self._fire(side, effect)  # instant

        self._q.put(
            (
                "warn_finish",
                side,
                cfg.HAPTIC_WARN_PULSES,
                cfg.HAPTIC_WARN_ON_S,
                cfg.HAPTIC_WARN_OFF_S,
                effect,
            )
        )

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._q.get(timeout=0.02)
            except queue.Empty:
                continue
            kind = item[0]
            if kind == "pulse":
                _, side, on_s, effect = item
                self._fire(side, effect)
                time.sleep(on_s)
                self._halt(side)
            elif kind == "pattern":
                _, side, pulses, on_s, off_s, effect = item
                for i in range(pulses):
                    if self._stop.is_set():
                        break
                    self._fire(side, effect)
                    time.sleep(on_s)
                    self._halt(side)
                    if i + 1 < pulses:
                        time.sleep(off_s)
            elif kind == "warn_finish":
                _, side, pulses, on_s, off_s, effect = item
                # First effect already playing from warn()
                time.sleep(on_s)
                self._halt(side)
                for i in range(1, pulses):
                    if self._stop.is_set():
                        break
                    time.sleep(off_s)
                    self._fire(side, effect)
                    time.sleep(on_s)
                    self._halt(side)
            elif kind == "stop":
                break

    def close(self) -> None:
        self._stop.set()
        self._q.put(("stop",))
        self._worker.join(timeout=2.0)
        for d in self._devs.values():
            d.close()
        self._devs.clear()
