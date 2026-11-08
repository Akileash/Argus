"""MPU-6050 driver (smbus2) and wear-gated fall detector."""

from __future__ import annotations

import math
import struct
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, List, Optional, Tuple

from smbus2 import SMBus

from hat import config as cfg
from hat.wear_state import WearState

# Register map
PWR_MGMT_1 = 0x6B
SMPLRT_DIV = 0x19
CONFIG = 0x1A
GYRO_CONFIG = 0x1B
ACCEL_CONFIG = 0x1C
ACCEL_XOUT_H = 0x3B
WHO_AM_I = 0x75


@dataclass
class ImuSample:
    t: float
    ax: float
    ay: float
    az: float
    gx: float
    gy: float
    gz: float

    @property
    def mag(self) -> float:
        return math.sqrt(self.ax * self.ax + self.ay * self.ay + self.az * self.az)


class MPU6050:
    """Raw smbus2 wrapper: ±16 g, ±1000 dps, DLPF ~44 Hz."""

    def __init__(self, bus_id: int = cfg.I2C_BUS, addr: int = cfg.MPU_ADDR):
        self.addr = addr
        self.bus_id = bus_id
        self.bus = SMBus(bus_id)
        self.i2c_errors = 0
        self._setup()

    def _reopen(self) -> None:
        try:
            self.bus.close()
        except OSError:
            pass
        time.sleep(0.02)
        self.bus = SMBus(self.bus_id)

    def _w(self, reg: int, val: int, retries: int = 8) -> None:
        last: Optional[OSError] = None
        for i in range(retries):
            try:
                self.bus.write_byte_data(self.addr, reg, val & 0xFF)
                return
            except OSError as e:
                last = e
                self.i2c_errors += 1
                time.sleep(0.002 * (i + 1))
                if i == retries // 2:
                    self._reopen()
        assert last is not None
        raise last

    def _r(self, reg: int, retries: int = 8) -> int:
        last: Optional[OSError] = None
        for i in range(retries):
            try:
                return self.bus.read_byte_data(self.addr, reg)
            except OSError as e:
                last = e
                self.i2c_errors += 1
                time.sleep(0.002 * (i + 1))
                if i == retries // 2:
                    self._reopen()
        assert last is not None
        raise last

    def _read_block14(self) -> bytes:
        """14-byte burst; fall back to word reads if the bus hates block xfers."""
        last: Optional[OSError] = None
        for i in range(6):
            try:
                return bytes(self.bus.read_i2c_block_data(self.addr, ACCEL_XOUT_H, 14))
            except OSError as e:
                last = e
                self.i2c_errors += 1
                time.sleep(0.002 * (i + 1))
                if i == 2:
                    self._reopen()
        # Fallback: 7 × 16-bit register pairs (more tolerant on flaky wiring)
        out = bytearray()
        for reg in range(ACCEL_XOUT_H, ACCEL_XOUT_H + 14, 2):
            hi = self._r(reg)
            lo = self._r(reg + 1)
            out.append(hi)
            out.append(lo)
        if last is not None and not out:
            raise last
        return bytes(out)

    def _setup(self) -> None:
        self._w(PWR_MGMT_1, 0x00)  # wake
        time.sleep(0.1)
        # DLPF_CFG=3 → accel ~44 Hz, gyro ~42 Hz
        self._w(CONFIG, 0x03)
        # Sample rate ≈ 1 kHz / (1 + div); with DLPF on, gyro output rate 1 kHz
        # div=4 → ~200 Hz
        self._w(SMPLRT_DIV, 0x04)
        self._w(GYRO_CONFIG, 0x10)   # ±1000 dps
        self._w(ACCEL_CONFIG, 0x18)  # ±16 g
        time.sleep(0.05)

    def who_am_i(self) -> int:
        return self._r(WHO_AM_I)

    def read(self, t: Optional[float] = None) -> ImuSample:
        raw = self._read_block14()
        ax, ay, az, _temp, gx, gy, gz = struct.unpack(">hhhhhhh", raw)
        if t is None:
            t = time.perf_counter()
        return ImuSample(
            t=t,
            ax=ax / cfg.ACCEL_LSB_PER_G,
            ay=ay / cfg.ACCEL_LSB_PER_G,
            az=az / cfg.ACCEL_LSB_PER_G,
            gx=gx / cfg.GYRO_LSB_PER_DPS,
            gy=gy / cfg.GYRO_LSB_PER_DPS,
            gz=gz / cfg.GYRO_LSB_PER_DPS,
        )

    def close(self) -> None:
        try:
            self.bus.close()
        except OSError:
            pass


@dataclass
class Detection:
    type: str  # always "fall" (impact path removed)
    peak_accel_g: float
    freefall_ms: float
    confidence: str
    imu_trace: List[List[float]]
    helmet_worn_at_onset: bool = False
    helmet_worn_lookback_s: float = 0.0
    wear_on_fraction: float = 0.0
    suppressed: bool = False
    suppress_reason: str = ""


class FallImpactDetector:
    """
    Person-fall signature (IMU) + optional ToF wear gate.

    Everything actionable is a `fall` (no separate impact type):
      idle → ( |a| < FREEFALL_G for FREEFALL_MIN_S ) → freefall
           → hard land / soft settle → FALL
      idle → ( |a| >= IMPACT_G ) → FALL (hard hit without long freefall)

    If wear_state is set and WEAR_GATE_ENABLE: only emit when helmet was
    ON beforehand. No IMU hat_removed path.
    """

    def __init__(self, wear_state: Optional[WearState] = None) -> None:
        # Shared mutable float holder: haptics calls note_buzz()
        self._last_buzz_holder = {"t": 0.0}
        self.wear_state = wear_state
        self.suppressed_falls = 0
        self.suppressed_impacts = 0  # kept for summary compat; always 0
        n = int(cfg.IMU_BUFFER_S * cfg.IMU_SAMPLE_HZ) + 8
        self._buf: Deque[ImuSample] = deque(maxlen=n)
        self._state = "idle"
        self._ff_start: Optional[float] = None
        self._ff_ms = 0.0
        self._impact_t: Optional[float] = None
        self._peak = 0.0
        self._still_start: Optional[float] = None
        self._cooldown_until = 0.0
        self._last_error_log = 0.0

    @property
    def last_buzz_holder(self) -> dict:
        return self._last_buzz_holder

    def note_buzz(self) -> None:
        self._last_buzz_holder["t"] = time.perf_counter()

    def push(self, sample: ImuSample) -> Optional[Detection]:
        self._buf.append(sample)
        now = sample.t

        # Ignore IMU for a short window after haptic pulse
        if now - self._last_buzz_holder["t"] < cfg.IMU_IGNORE_AFTER_BUZZ_S:
            return None
        if now < self._cooldown_until:
            return None

        mag = sample.mag

        if self._state == "idle":
            if mag < cfg.FREEFALL_G:
                if self._ff_start is None:
                    self._ff_start = now
                elif (now - self._ff_start) >= cfg.FREEFALL_MIN_S:
                    self._state = "freefall"
                    self._ff_ms = (now - self._ff_start) * 1000.0
                    self._peak = mag
            else:
                self._ff_start = None
                # Hard hit with little/no freefall → still a fall (no impact type)
                if mag >= cfg.IMPACT_G:
                    self._peak = mag
                    self._ff_ms = 0.0
                    self._ff_start = now
                    return self._emit_fall(now, confidence="high")
            return None

        if self._state == "freefall":
            self._ff_ms = max(self._ff_ms, (now - (self._ff_start or now)) * 1000.0)
            self._peak = max(self._peak, mag)
            if mag >= cfg.IMPACT_AFTER_FF_G:
                self._state = "wait_still"
                self._impact_t = now
                self._still_start = None
                return None
            # Soft landing: left freefall and settled near 1 g without a hard spike
            soft_need = getattr(cfg, "SOFT_LANDING_AFTER_FF_S", 0.080)
            if (
                self._ff_ms >= soft_need * 1000.0
                and abs(mag - 1.0) <= cfg.STILL_BAND_G
            ):
                self._peak = max(self._peak, mag, 1.0)
                return self._emit_fall(now, confidence="low")
            if self._ff_start and (now - self._ff_start) > cfg.IMPACT_AFTER_FF_WINDOW_S + cfg.FREEFALL_MIN_S:
                self._reset_state()
            return None

        if self._state == "wait_still":
            self._peak = max(self._peak, mag)
            assert self._impact_t is not None
            if abs(mag - 1.0) <= cfg.STILL_BAND_G:
                if self._still_start is None:
                    self._still_start = now
                elif (now - self._still_start) >= cfg.STILL_HOLD_S:
                    return self._emit_fall(now, confidence="high")
            else:
                self._still_start = None

            if (now - self._impact_t) >= cfg.FALL_STILL_DEADLINE_S:
                if self._peak >= getattr(cfg, "FALL_MIN_PEAK_G", 5.0):
                    return self._emit_fall(now, confidence="low")
                self._reset_state()
            return None

        return None

    def _wear_gate(self, onset_t: float) -> Tuple[bool, float, float, str]:
        """Return (allow, worn_at_onset, on_fraction, reason)."""
        if not getattr(cfg, "WEAR_GATE_ENABLE", True):
            return True, True, 1.0, "gate_disabled"
        if self.wear_state is None:
            # No ToF wired into detector → allow (bring-up / unit tests)
            return True, False, 0.0, "no_wear_state"
        lookback = getattr(cfg, "WEAR_GATE_LOOKBACK_S", 3.0)
        min_frac = getattr(cfg, "WEAR_GATE_MIN_ON_FRACTION", 0.5)
        g = self.wear_state.gate_fall(
            onset_t, lookback_s=lookback, min_on_fraction=min_frac
        )
        return g.allow, g.helmet_worn_at_onset, g.on_fraction, g.reason

    def _emit_fall(self, now: float, confidence: str) -> Optional[Detection]:
        min_peak = getattr(cfg, "FALL_MIN_PEAK_G", 5.0)
        if self._peak < min_peak:
            self._reset_state()
            return None
        onset = self._ff_start if self._ff_start is not None else now
        allow, worn_at, frac, reason = self._wear_gate(onset)
        lookback = getattr(cfg, "WEAR_GATE_LOOKBACK_S", 3.0)
        if not allow:
            self.suppressed_falls += 1
            det = Detection(
                type="fall",
                peak_accel_g=self._peak,
                freefall_ms=self._ff_ms,
                confidence=confidence,
                imu_trace=self._trace_relative(now),
                helmet_worn_at_onset=worn_at,
                helmet_worn_lookback_s=lookback,
                wear_on_fraction=frac,
                suppressed=True,
                suppress_reason=reason,
            )
            self._cooldown_until = now + cfg.IMU_COOLDOWN_S
            self._reset_state()
            return det
        det = Detection(
            type="fall",
            peak_accel_g=self._peak,
            freefall_ms=self._ff_ms,
            confidence=confidence,
            imu_trace=self._trace_relative(now),
            helmet_worn_at_onset=worn_at,
            helmet_worn_lookback_s=lookback,
            wear_on_fraction=frac,
        )
        self._cooldown_until = now + cfg.IMU_COOLDOWN_S
        self._reset_state()
        return det

    def _reset_state(self) -> None:
        self._state = "idle"
        self._ff_start = None
        self._ff_ms = 0.0
        self._impact_t = None
        self._peak = 0.0
        self._still_start = None

    def _trace_relative(self, t0: float) -> List[List[float]]:
        """Ring buffer as [t_ms, ax, ay, az] relative to event time."""
        out: List[List[float]] = []
        for s in self._buf:
            out.append([
                round((s.t - t0) * 1000.0, 1),
                round(s.ax, 3),
                round(s.ay, 3),
                round(s.az, 3),
            ])
        return out

    def rate_test(self, seconds: float = 2.0) -> Tuple[float, int]:
        """Return (hz, i2c_errors) over a short burst."""
        mpu = MPU6050()
        errors = 0
        n = 0
        t0 = time.perf_counter()
        try:
            while time.perf_counter() - t0 < seconds:
                try:
                    mpu.read()
                    n += 1
                except OSError:
                    errors += 1
        finally:
            mpu.close()
        elapsed = max(time.perf_counter() - t0, 1e-6)
        return n / elapsed, errors
