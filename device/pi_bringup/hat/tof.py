"""VL6180X ToF wear reader for the on-hat runtime (smbus2).

Updates a shared WearState. Wear dwell rules match pi_sensors bring-up.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Tuple

from smbus2 import SMBus

from hat import config as cfg
from hat.wear_state import WearState

# Registers
IDENTIFICATION__MODEL_ID = 0x000
SYSTEM__INTERRUPT_CONFIG_GPIO = 0x014
SYSTEM__INTERRUPT_CLEAR = 0x015
SYSTEM__FRESH_OUT_OF_RESET = 0x016
SYSRANGE__START = 0x018
SYSRANGE__INTERMEASUREMENT_PERIOD = 0x01B
SYSRANGE__MAX_CONVERGENCE_TIME = 0x01C
SYSRANGE__CROSSTALK_VALID_HEIGHT = 0x021
SYSRANGE__PART_TO_PART_RANGE_OFFSET = 0x024
SYSRANGE__RANGE_CHECK_ENABLES = 0x02D
SYSRANGE__VHV_RECALIBRATE = 0x02E
SYSRANGE__VHV_REPEAT_RATE = 0x031
SYSALS__INTERMEASUREMENT_PERIOD = 0x03E
SYSALS__ANALOGUE_GAIN = 0x03F
SYSALS__INTEGRATION_PERIOD = 0x040
RESULT__RANGE_STATUS = 0x04D
RESULT__INTERRUPT_STATUS_GPIO = 0x04F
RESULT__RANGE_VAL = 0x062
RANGE_SCALER = 0x096
READOUT__AVERAGING_SAMPLE_PERIOD = 0x10A
INTERLEAVED_MODE__ENABLE = 0x2A3

MODEL_ID = 0xB4

PRIVATE_TUNING = (
    (0x207, 0x01),
    (0x208, 0x01),
    (0x096, 0x00),
    (0x097, 0xFD),
    (0x0E3, 0x01),
    (0x0E4, 0x03),
    (0x0E5, 0x02),
    (0x0E6, 0x01),
    (0x0E7, 0x03),
    (0x0F5, 0x02),
    (0x0D9, 0x05),
    (0x0DB, 0xCE),
    (0x0DC, 0x03),
    (0x0DD, 0xF8),
    (0x09F, 0x00),
    (0x0A3, 0x3C),
    (0x0B7, 0x00),
    (0x0BB, 0x3C),
    (0x0B2, 0x09),
    (0x0CA, 0x09),
    (0x198, 0x01),
    (0x1B0, 0x17),
    (0x1AD, 0x00),
    (0x0FF, 0x05),
    (0x100, 0x05),
    (0x199, 0x05),
    (0x1A6, 0x1B),
    (0x1AC, 0x3E),
    (0x1A7, 0x1F),
    (0x030, 0x00),
)


@dataclass
class WearSnapshot:
    helmet_worn: bool
    near: bool
    distance_mm: Optional[int]
    status: int
    changed: bool


class WearDetector:
    """Near-band + ON/OFF dwell → helmet_worn."""

    def __init__(self) -> None:
        self.min_mm = cfg.TOF_NEAR_MM_MIN
        self.near_mm = cfg.TOF_NEAR_MM_MAX
        self.on_dwell_ms = cfg.TOF_ON_DWELL_MS
        self.off_dwell_ms = cfg.TOF_OFF_DWELL_MS
        self.wear_on = False
        self.near_timing = False
        self.far_timing = False
        self.near_start = 0.0
        self.far_start = 0.0
        self.last_mm: Optional[int] = None
        self.last_near = False
        self.last_status = 7

    def update(self, mm: Optional[int], status: int, now: float) -> WearSnapshot:
        valid = (status == 0) and (mm is not None) and (mm != 255)
        near = bool(valid and mm is not None and self.min_mm <= mm <= self.near_mm)
        self.last_status = status
        self.last_near = near
        if valid:
            self.last_mm = mm

        if near:
            self.far_timing = False
            if not self.near_timing:
                self.near_timing = True
                self.near_start = now
        else:
            self.near_timing = False
            if not self.far_timing:
                self.far_timing = True
                self.far_start = now

        changed = False
        if (not self.wear_on) and self.near_timing and (now - self.near_start) * 1000.0 >= self.on_dwell_ms:
            self.wear_on = True
            changed = True
        elif self.wear_on and self.far_timing and (now - self.far_start) * 1000.0 >= self.off_dwell_ms:
            self.wear_on = False
            changed = True

        return WearSnapshot(
            helmet_worn=self.wear_on,
            near=near,
            distance_mm=self.last_mm if valid else None,
            status=status,
            changed=changed,
        )


class VL6180X:
    def __init__(self, bus_id: int = cfg.I2C_BUS_TOF, addr: int = cfg.TOF_ADDR):
        self.addr = addr
        self.bus = SMBus(bus_id)

    def _w8(self, reg: int, val: int) -> None:
        from smbus2 import i2c_msg

        msg = i2c_msg.write(self.addr, bytes([(reg >> 8) & 0xFF, reg & 0xFF, val & 0xFF]))
        self.bus.i2c_rdwr(msg)

    def _r8(self, reg: int) -> int:
        from smbus2 import i2c_msg

        wr = i2c_msg.write(self.addr, bytes([(reg >> 8) & 0xFF, reg & 0xFF]))
        rd = i2c_msg.read(self.addr, 1)
        self.bus.i2c_rdwr(wr, rd)
        return list(rd)[0] & 0xFF

    def _w16(self, reg: int, val: int) -> None:
        from smbus2 import i2c_msg

        msg = i2c_msg.write(
            self.addr,
            bytes([(reg >> 8) & 0xFF, reg & 0xFF, (val >> 8) & 0xFF, val & 0xFF]),
        )
        self.bus.i2c_rdwr(msg)

    def model_id(self) -> int:
        return self._r8(IDENTIFICATION__MODEL_ID)

    def init(self) -> None:
        if self._r8(SYSTEM__FRESH_OUT_OF_RESET) == 1:
            for reg, val in PRIVATE_TUNING:
                self._w8(reg, val)
            self._w8(SYSTEM__FRESH_OUT_OF_RESET, 0)
        self._w8(READOUT__AVERAGING_SAMPLE_PERIOD, 0x30)
        self._w8(SYSALS__ANALOGUE_GAIN, 0x46)
        self._w8(SYSRANGE__VHV_REPEAT_RATE, 0xFF)
        self._w16(SYSALS__INTEGRATION_PERIOD, 0x0063)
        self._w8(SYSRANGE__VHV_RECALIBRATE, 0x01)
        self._w8(SYSRANGE__INTERMEASUREMENT_PERIOD, 0x09)
        self._w8(SYSALS__INTERMEASUREMENT_PERIOD, 0x31)
        self._w8(SYSTEM__INTERRUPT_CONFIG_GPIO, 0x24)
        self._w8(SYSRANGE__MAX_CONVERGENCE_TIME, 0x31)
        self._w8(INTERLEAVED_MODE__ENABLE, 0)
        self._w8(RANGE_SCALER, 253)  # scaling 1
        checks = self._r8(SYSRANGE__RANGE_CHECK_ENABLES)
        self._w8(SYSRANGE__RANGE_CHECK_ENABLES, checks & ~0x01)
        self._w8(SYSTEM__INTERRUPT_CLEAR, 0x07)

    def read_range_single(self, timeout_ms: float = 500.0) -> Tuple[int, int, bool]:
        self._w8(SYSRANGE__START, 0x01)
        t0 = time.perf_counter()
        while True:
            if (self._r8(RESULT__INTERRUPT_STATUS_GPIO) & 0x07) == 0x04:
                break
            if (time.perf_counter() - t0) * 1000.0 > timeout_ms:
                return 255, 255, True
            time.sleep(0.001)
        mm = self._r8(RESULT__RANGE_VAL)
        self._w8(SYSTEM__INTERRUPT_CLEAR, 0x01)
        status = (self._r8(RESULT__RANGE_STATUS) >> 4) & 0x0F
        return mm, status, False

    def close(self) -> None:
        try:
            self.bus.close()
        except OSError:
            pass


class TofWearMonitor:
    """Poll VL6180X and push wear updates into WearState."""

    def __init__(self, wear: WearState):
        self.wear = wear
        self.driver = VL6180X()
        self.detector = WearDetector()
        self._ok = False

    def start(self) -> bool:
        try:
            mid = self.driver.model_id()
            if mid != MODEL_ID:
                print(f"[tof] unexpected model 0x{mid:02X} (want 0x{MODEL_ID:02X})", flush=True)
                return False
            self.driver.init()
            self._ok = True
            print(
                f"[tof] VL6180X ready  band={cfg.TOF_NEAR_MM_MIN}-{cfg.TOF_NEAR_MM_MAX} mm  "
                f"on_dwell={cfg.TOF_ON_DWELL_MS}ms",
                flush=True,
            )
            return True
        except OSError as e:
            print(f"[tof] open/init failed: {e}", flush=True)
            return False

    def poll_once(self) -> WearSnapshot:
        now = time.perf_counter()
        if not self._ok:
            snap = self.detector.update(None, 255, now)
            self.wear.update(False, t=now, distance_mm=None, near=False)
            return snap
        try:
            mm, status, timed_out = self.driver.read_range_single()
            if timed_out:
                snap = self.detector.update(None, 255, now)
            else:
                snap = self.detector.update(mm, status, now)
        except OSError:
            snap = self.detector.update(None, 255, now)
        self.wear.update(
            snap.helmet_worn,
            t=now,
            distance_mm=snap.distance_mm,
            near=snap.near,
        )
        return snap

    def close(self) -> None:
        self.driver.close()
