"""VL6180X driver + wear state machine.

Ported register-for-register from Pololu VL6180X 1.4.0 and
TimeOfFlight/wear_detect/wear_detect.ino.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ..base import Sensor
from ..bus import I2CBus, SensorFault
from .. import events
from ..sinks import Sink

# Registers (Pololu / ST)
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
DEFAULT_ADDR = 0x29

# Wear constants — helmet-on band (mm).
# Live hat reads ~63–69 mm when occupied; floor must sit below that.
# VL6180X reports 0–255; 255 is the no-target sentinel, so max valid = 254.
MIN_MM = 50
NEAR_MM = 254
ON_DWELL_MS = 5000
OFF_DWELL_MS = 3000
HEARTBEAT_MS = 500
SAMPLE_GAP_MS = 50
FAILS_BEFORE_RECOVER = 8
RECOVER_COOLDOWN_MS = 2000
RANGE_TIMEOUT_MS = 500

# ST AN4545 mandatory private registers (Pololu init when FRESH_OUT_OF_RESET==1)
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

SCALER_VALUES = {1: 253, 2: 127, 3: 84}


@dataclass
class WearSnapshot:
    helmet_worn: bool
    state: str
    distance_mm: Optional[int]
    status: int
    near: bool
    sensor_ok: bool
    on_in_ms: Optional[int] = None
    off_in_ms: Optional[int] = None
    changed: bool = False


class WearDetector:
    """Pure state machine. Feed it (mm, status, fault, now_s)."""

    def __init__(
        self,
        min_mm: int = MIN_MM,
        near_mm: int = NEAR_MM,
        on_dwell_ms: int = ON_DWELL_MS,
        off_dwell_ms: int = OFF_DWELL_MS,
    ) -> None:
        self.min_mm = min_mm
        self.near_mm = near_mm
        self.on_dwell_ms = on_dwell_ms
        self.off_dwell_ms = off_dwell_ms
        self.wear_on = False
        self.near_timing = False
        self.far_timing = False
        self.near_start = 0.0
        self.far_start = 0.0
        self.last_range_mm: Optional[int] = None
        self.last_range_valid = False
        self.last_status = 7
        self.last_near = False
        self.sensor_ok = False

    def update(
        self,
        *,
        mm: Optional[int],
        status: int,
        fault: bool,
        now: float,
    ) -> WearSnapshot:
        changed = False
        if fault:
            self.last_status = 255
            self.last_near = False
            self.last_range_valid = False
            self.near_timing = False
            self.sensor_ok = False
            return self.snapshot(now, changed=False)

        self.sensor_ok = True
        valid = (status == 0) and (mm is not None) and (mm != 255)
        near = bool(valid and mm is not None and self.min_mm <= mm <= self.near_mm)

        self.last_status = status
        self.last_near = near
        self.last_range_valid = valid
        if valid and mm is not None:
            self.last_range_mm = mm

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

        if (not self.wear_on) and self.near_timing and (now - self.near_start) * 1000.0 >= self.on_dwell_ms:
            self.wear_on = True
            changed = True
        elif self.wear_on and self.far_timing and (now - self.far_start) * 1000.0 >= self.off_dwell_ms:
            self.wear_on = False
            changed = True

        return self.snapshot(now, changed=changed)

    def snapshot(self, now: float, changed: bool = False) -> WearSnapshot:
        on_in: Optional[int] = None
        off_in: Optional[int] = None
        if (not self.wear_on) and self.near_timing:
            on_in = max(0, int(self.on_dwell_ms - (now - self.near_start) * 1000.0))
        elif self.wear_on and self.far_timing:
            off_in = max(0, int(self.off_dwell_ms - (now - self.far_start) * 1000.0))
        return WearSnapshot(
            helmet_worn=self.wear_on,
            state="ON" if self.wear_on else "OFF",
            distance_mm=self.last_range_mm if self.last_range_valid else None,
            status=self.last_status,
            near=self.last_near,
            sensor_ok=self.sensor_ok,
            on_in_ms=on_in,
            off_in_ms=off_in,
            changed=changed,
        )


class VL6180X:
    """Low-level register driver."""

    def __init__(self, bus: I2CBus, address: int = DEFAULT_ADDR) -> None:
        self.bus = bus
        self.address = address
        self.ptp_offset = 0
        self.scaling = 1

    def read_reg(self, reg: int) -> int:
        return self.bus.read_u8(self.address, reg)

    def write_reg(self, reg: int, value: int) -> None:
        self.bus.write_u8(self.address, reg, value)

    def read_reg16(self, reg: int) -> int:
        return self.bus.read_u16(self.address, reg)

    def write_reg16(self, reg: int, value: int) -> None:
        self.bus.write_u16(self.address, reg, value)

    def init(self) -> None:
        """Pololu VL6180X::init() — private tuning only when fresh out of reset."""
        self.ptp_offset = self.read_reg(SYSRANGE__PART_TO_PART_RANGE_OFFSET)
        if self.read_reg(SYSTEM__FRESH_OUT_OF_RESET) == 1:
            self.scaling = 1
            for reg, val in PRIVATE_TUNING:
                self.write_reg(reg, val)
            self.write_reg(SYSTEM__FRESH_OUT_OF_RESET, 0)
        else:
            s = self.read_reg16(RANGE_SCALER)
            if s == SCALER_VALUES[3]:
                self.scaling = 3
            elif s == SCALER_VALUES[2]:
                self.scaling = 2
            else:
                self.scaling = 1
            self.ptp_offset *= self.scaling

    def set_scaling(self, new_scaling: int) -> None:
        if new_scaling < 1 or new_scaling > 3:
            return
        self.scaling = new_scaling
        self.write_reg16(RANGE_SCALER, SCALER_VALUES[self.scaling])
        self.write_reg(SYSRANGE__PART_TO_PART_RANGE_OFFSET, self.ptp_offset // self.scaling)
        self.write_reg(SYSRANGE__CROSSTALK_VALID_HEIGHT, 20 // self.scaling)
        rce = self.read_reg(SYSRANGE__RANGE_CHECK_ENABLES)
        self.write_reg(SYSRANGE__RANGE_CHECK_ENABLES, (rce & 0xFE) | (1 if self.scaling == 1 else 0))

    def configure_default(self) -> None:
        """Pololu VL6180X::configureDefault() — always run on every start."""
        self.write_reg(READOUT__AVERAGING_SAMPLE_PERIOD, 0x30)
        self.write_reg(SYSALS__ANALOGUE_GAIN, 0x46)
        self.write_reg(SYSRANGE__VHV_REPEAT_RATE, 0xFF)
        self.write_reg16(SYSALS__INTEGRATION_PERIOD, 0x0063)
        self.write_reg(SYSRANGE__VHV_RECALIBRATE, 0x01)
        self.write_reg(SYSRANGE__INTERMEASUREMENT_PERIOD, 0x09)
        self.write_reg(SYSALS__INTERMEASUREMENT_PERIOD, 0x31)
        self.write_reg(SYSTEM__INTERRUPT_CONFIG_GPIO, 0x24)
        self.write_reg(SYSRANGE__MAX_CONVERGENCE_TIME, 0x31)
        self.write_reg(INTERLEAVED_MODE__ENABLE, 0)
        self.set_scaling(1)

    def read_range_single(self, timeout_ms: int = RANGE_TIMEOUT_MS) -> Tuple[int, int, bool]:
        """Single-shot range. Returns (mm, status, timed_out)."""
        self.write_reg(SYSRANGE__START, 0x01)
        t0 = time.monotonic()
        while True:
            status_gpio = self.read_reg(RESULT__INTERRUPT_STATUS_GPIO)
            if (status_gpio & 0x07) == 0x04:
                break
            if (time.monotonic() - t0) * 1000.0 > timeout_ms:
                return 255, 255, True
            time.sleep(0.001)
        mm = self.read_reg(RESULT__RANGE_VAL)
        self.write_reg(SYSTEM__INTERRUPT_CLEAR, 0x01)
        status = (self.read_reg(RESULT__RANGE_STATUS) >> 4) & 0x0F
        return mm, status, False


class VL6180XWearSensor(Sensor):
    name = "tof"
    address = DEFAULT_ADDR
    heartbeat_ms = HEARTBEAT_MS
    poll_gap_ms = float(SAMPLE_GAP_MS)
    fails_before_recover = FAILS_BEFORE_RECOVER
    recover_cooldown_ms = RECOVER_COOLDOWN_MS

    def __init__(self, bus: I2CBus, sink: Sink, address: int = DEFAULT_ADDR) -> None:
        super().__init__(bus, sink)
        self.address = address
        self.driver = VL6180X(bus, address)
        self.detector = WearDetector()
        self._snap = self.detector.snapshot(time.monotonic())

    def init_sensor(self) -> bool:
        try:
            model = self.driver.read_reg(IDENTIFICATION__MODEL_ID)
            if model != MODEL_ID:
                return False
            self.driver.init()
            self.driver.configure_default()
            checks = self.driver.read_reg(SYSRANGE__RANGE_CHECK_ENABLES)
            self.driver.write_reg(SYSRANGE__RANGE_CHECK_ENABLES, checks & ~0x01)
            self.driver.write_reg(SYSTEM__INTERRUPT_CLEAR, 0x07)
            return True
        except SensorFault:
            return False

    def make_hello(self, now: float) -> Dict[str, Any]:
        s = self._snap
        return events.hello_tof(
            helmet_worn=s.helmet_worn,
            state=s.state,
            distance_mm=s.distance_mm,
            status=s.status,
            near=s.near,
            sensor_ok=self.sensor_ok,
            recoveries=self.recoveries,
            near_mm_min=MIN_MM,
            near_mm_max=NEAR_MM,
            on_dwell_ms=ON_DWELL_MS,
            off_dwell_ms=OFF_DWELL_MS,
            now=now,
        )

    def make_sample(self, now: float) -> Optional[Dict[str, Any]]:
        s = self.detector.snapshot(now)
        self._snap = s
        return events.sample_tof(
            helmet_worn=s.helmet_worn,
            state=s.state,
            distance_mm=s.distance_mm,
            status=s.status,
            near=s.near,
            sensor_ok=self.sensor_ok and s.sensor_ok,
            recoveries=self.recoveries,
            on_in_ms=s.on_in_ms,
            off_in_ms=s.off_in_ms,
            now=now,
        )

    def poll(self, now: float) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        try:
            mm, status, timed_out = self.driver.read_range_single()
            fault = timed_out
        except SensorFault:
            mm, status, fault = None, 255, True

        if fault:
            snap = self.detector.update(mm=None, status=255, fault=True, now=now)
            self._snap = snap
            rec = self.note_fault(now)
            if rec:
                out.append(rec)
            return out

        self.note_ok()
        snap = self.detector.update(mm=mm, status=status, fault=False, now=now)
        self._snap = snap
        if snap.changed:
            out.append(
                events.wear_event(
                    helmet_worn=snap.helmet_worn,
                    state=snap.state,
                    distance_mm=snap.distance_mm,
                    status=snap.status,
                    near=snap.near,
                    sensor_ok=True,
                    recoveries=self.recoveries,
                    on_in_ms=snap.on_in_ms,
                    off_in_ms=snap.off_in_ms,
                    now=now,
                )
            )
        return out
