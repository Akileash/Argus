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
ACCEL_CONFIG2 = 0x1D  # MPU-6500 family only (accel DLPF)
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
        self.config_losses = 0
        self.last_recovery_t = 0.0
        self._expect: dict = {}
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
        # Boards sold as "MPU-6050" are often MPU-6500 family (WHO_AM_I 0x70).
        # On those, CONFIG only filters the gyro: the accel DLPF lives in
        # ACCEL_CONFIG2 and defaults to 218 Hz bandwidth (aliasing at 200 Hz).
        self._expect = {
            SMPLRT_DIV: (0x04, 0xFF),
            CONFIG: (0x03, 0xFF),
            GYRO_CONFIG: (0x10, 0xFF),
            ACCEL_CONFIG: (0x18, 0xFF),
        }
        if self.who_am_i() in cfg.MPU6500_FAMILY_IDS:
            self._w(ACCEL_CONFIG2, 0x03)  # A_DLPF_CFG=3 → ~44.8 Hz
            self._expect[ACCEL_CONFIG2] = (0x03, 0x0F)
        time.sleep(0.05)

    def check_config(self) -> bool:
        """Read back the config registers (2 short I2C transactions).

        Returns True if everything is as `_setup` left it. On a mismatch
        (chip reset / brown-out / I2C glitch → it falls back to ±2 g, which
        makes every reading 8x too large at our 2048 LSB/g scaling) it
        re-runs `_setup`, logs loudly and returns False so the caller can
        discard the samples taken while the config was wrong.
        Raises OSError only if the bus itself is unusable.
        """
        first = SMPLRT_DIV
        n = ACCEL_CONFIG2 - first + 1  # 0x19..0x1D
        block = bytes(self._read_regs(first, n))
        pwr = self._r(PWR_MGMT_1)
        bad = []
        for reg, (want, mask) in self._expect.items():
            got = block[reg - first]
            if (got & mask) != (want & mask):
                bad.append(f"0x{reg:02X}=0x{got:02X}(want 0x{want:02X})")
        if pwr & 0xE0:  # DEVICE_RESET / SLEEP / CYCLE
            bad.append(f"PWR_MGMT_1=0x{pwr:02X}")
        if not bad:
            return True
        self.config_losses += 1
        print(
            f"[imu] CONFIG LOST ({', '.join(bad)}) — re-initialising MPU "
            f"(loss #{self.config_losses}); recent samples are discarded",
            flush=True,
        )
        self._setup()
        self.last_recovery_t = time.perf_counter()
        block = bytes(self._read_regs(first, n))
        still = [
            f"0x{r:02X}" for r, (w, m) in self._expect.items()
            if (block[r - first] & m) != (w & m)
        ]
        if still:
            print(f"[imu] re-init did NOT stick: {still}", flush=True)
        else:
            print("[imu] config recovered OK", flush=True)
        return False

    def _read_regs(self, reg: int, n: int, retries: int = 6) -> List[int]:
        last: Optional[OSError] = None
        for i in range(retries):
            try:
                return list(self.bus.read_i2c_block_data(self.addr, reg, n))
            except OSError as e:
                last = e
                self.i2c_errors += 1
                time.sleep(0.002 * (i + 1))
                if i == retries // 2:
                    self._reopen()
        assert last is not None
        raise last

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

    Everything actionable is a `fall` (no separate impact type).

      idle --(|a| < FREEFALL_G for FREEFALL_MIN_S)--> freefall
      freefall --(|a| >= IMPACT_AFTER_FF_G within window)--> wait_still
      freefall --(settles to ~1 g after >= SOFT_LANDING_AFTER_FF_S)--> wait_still
      idle --(|a| >= IMPACT_G, no freefall)--> wait_still
      wait_still --(|a|~1 g for STILL_HOLD_S)--> check head tilt vs pre-fall
          gravity (>= FALL_TILT_MIN_DEG unless freefall was long) --> FALL

    Not a fall: a hit/jump after which the wearer keeps moving (no stillness
    within FALL_STILL_DEADLINE_S) or ends up in the same orientation.

    After an event the detector is disarmed until the signal has been quiet
    (~1 g) for REARM_QUIET_S, plus IMU_COOLDOWN_S, so one fall = one event.

    Samples are never trusted while the sensor config may be wrong:
    `config_lost()` purges the recent buffer and disarms, and a window whose
    resting magnitude is physically implausible (> IMU_IMPLAUSIBLE_G) blocks
    detection.

    If wear_state is set and WEAR_GATE_ENABLE: only emit when helmet was
    ON beforehand. No IMU hat_removed path.
    """

    def __init__(self, wear_state: Optional[WearState] = None) -> None:
        # Shared mutable float holder: haptics calls note_buzz()
        self._last_buzz_holder = {"t": 0.0}
        self.wear_state = wear_state
        self.suppressed_falls = 0
        self.suppressed_impacts = 0  # kept for summary compat; always 0
        self.config_purges = 0
        n = int(cfg.IMU_BUFFER_S * cfg.IMU_SAMPLE_HZ) + 8
        self._buf: Deque[ImuSample] = deque(maxlen=n)
        self._state = "idle"
        self._ff_start: Optional[float] = None
        self._ff_ms = 0.0
        self._impact_t: Optional[float] = None
        self._impacted = False
        self._onset_t: Optional[float] = None
        self._peak = 0.0
        self._still_start: Optional[float] = None
        self._cooldown_until = 0.0
        self._armed = True
        self._quiet_start: Optional[float] = None
        self._plaus: Deque[Tuple[float, bool]] = deque()
        self._plaus_big = 0
        self.implausible = False
        self._last_error_log = 0.0

    @property
    def last_buzz_holder(self) -> dict:
        return self._last_buzz_holder

    def note_buzz(self) -> None:
        self._last_buzz_holder["t"] = time.perf_counter()

    # -- config-loss handling -------------------------------------------
    def config_lost(self, now: Optional[float] = None) -> None:
        """Driver found/repaired a wrong sensor config: distrust recent data."""
        now = time.perf_counter() if now is None else now
        cutoff = now - cfg.IMU_CONFIG_LOSS_PURGE_S
        while self._buf and self._buf[-1].t >= cutoff:
            self._buf.pop()
        self.config_purges += 1
        self._reset_state()
        self._armed = False
        self._quiet_start = None
        self._cooldown_until = max(self._cooldown_until, now + cfg.IMU_SETTLE_AFTER_REINIT_S)
        self._plaus.clear()
        self._plaus_big = 0
        self.implausible = False

    def _track_plausibility(self, now: float, mag: float) -> None:
        big = mag > cfg.IMU_IMPLAUSIBLE_G
        self._plaus.append((now, big))
        self._plaus_big += big
        while self._plaus and now - self._plaus[0][0] > cfg.IMU_IMPLAUSIBLE_WINDOW_S:
            self._plaus_big -= self._plaus.popleft()[1]
        span = now - self._plaus[0][0]
        self.implausible = (
            span >= 0.5 * cfg.IMU_IMPLAUSIBLE_WINDOW_S
            and self._plaus_big >= 0.8 * len(self._plaus)
        )

    # -- main entry ------------------------------------------------------
    def push(self, sample: ImuSample) -> Optional[Detection]:
        self._buf.append(sample)
        now = sample.t
        mag = sample.mag

        self._track_plausibility(now, mag)
        if self.implausible:
            # e.g. a resting head cannot read ~8 g: the data is garbage.
            self._reset_state()
            self._armed = False
            self._quiet_start = None
            return None

        # Ignore IMU for a short window after haptic pulse
        if now - self._last_buzz_holder["t"] < cfg.IMU_IGNORE_AFTER_BUZZ_S:
            self._still_start = None
            return None

        if not self._armed:
            if abs(mag - 1.0) <= cfg.REARM_BAND_G:
                if self._quiet_start is None:
                    self._quiet_start = now
                elif (now - self._quiet_start) >= cfg.REARM_QUIET_S and now >= self._cooldown_until:
                    self._armed = True
            else:
                self._quiet_start = None
            return None
        if now < self._cooldown_until:
            return None

        if self._state == "idle":
            if mag < cfg.FREEFALL_G:
                if self._ff_start is None:
                    self._ff_start = now
                elif (now - self._ff_start) >= cfg.FREEFALL_MIN_S:
                    self._state = "freefall"
                    self._onset_t = self._ff_start
                    self._ff_ms = (now - self._ff_start) * 1000.0
                    self._peak = mag
            else:
                self._ff_start = None
                if mag >= cfg.IMPACT_G:
                    # Hard hit without freefall: only a fall if the wearer then
                    # lies still in a different orientation (checked below).
                    self._peak = mag
                    self._ff_ms = 0.0
                    self._onset_t = now
                    self._enter_wait_still(now, impacted=True)
            return None

        if self._state == "freefall":
            if mag < cfg.FREEFALL_G and self._ff_start is not None:
                self._ff_ms = max(self._ff_ms, (now - self._ff_start) * 1000.0)
            self._peak = max(self._peak, mag)
            if mag >= cfg.IMPACT_AFTER_FF_G:
                self._enter_wait_still(now, impacted=True)
                return None
            # Soft landing: left freefall and settled near 1 g without a spike
            if (
                self._ff_ms >= cfg.SOFT_LANDING_AFTER_FF_S * 1000.0
                and abs(mag - 1.0) <= cfg.STILL_BAND_G
            ):
                self._peak = max(self._peak, mag, 1.0)
                self._enter_wait_still(now, impacted=False)
                return None
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
                    return self._confirm(now)
            else:
                self._still_start = None
            if (now - self._impact_t) >= cfg.FALL_STILL_DEADLINE_S:
                # Kept moving: hit / jump / stumble, not a person lying still.
                self._reset_state()
            return None

        return None

    def _enter_wait_still(self, now: float, impacted: bool) -> None:
        self._state = "wait_still"
        self._impact_t = now
        self._impacted = impacted
        self._still_start = None

    # -- orientation -----------------------------------------------------
    def _mean_vec(self, t_lo: float, t_hi: float, min_n: int) -> Optional[Tuple[float, float, float]]:
        sx = sy = sz = 0.0
        n = 0
        for s in self._buf:
            if t_lo <= s.t <= t_hi:
                sx += s.ax
                sy += s.ay
                sz += s.az
                n += 1
        if n < min_n:
            return None
        return sx / n, sy / n, sz / n

    def _tilt_change_deg(self, onset: float, still_start: float, now: float) -> Optional[float]:
        """Angle between pre-fall and post-fall gravity (head orientation change)."""
        ref = self._mean_vec(onset - cfg.FALL_REF_WINDOW_S, onset - 0.15, 10)
        post = self._mean_vec(still_start, now, 10)
        if ref is None or post is None:
            return None
        nr = math.sqrt(sum(c * c for c in ref))
        npost = math.sqrt(sum(c * c for c in post))
        if not (0.7 <= nr <= 1.3) or not (0.7 <= npost <= 1.3):
            return None  # reference not a clean gravity vector
        dot = sum(a * b for a, b in zip(ref, post)) / (nr * npost)
        return math.degrees(math.acos(max(-1.0, min(1.0, dot))))

    def _confirm(self, now: float) -> Optional[Detection]:
        assert self._still_start is not None and self._onset_t is not None
        tilt = self._tilt_change_deg(self._onset_t, self._still_start, now)
        exempt = self._ff_ms >= cfg.FALL_TILT_EXEMPT_FF_S * 1000.0
        if tilt is not None and tilt < cfg.FALL_TILT_MIN_DEG and not exempt:
            if cfg.FALL_REQUIRE_TILT:
                self._reset_state()  # same orientation afterwards: jump / bump / nod
                return None
            return self._emit_fall(now, confidence="low")
        if tilt is None and not (self._impacted and self._ff_ms > 0.0):
            self._reset_state()  # can't verify orientation and weak evidence
            return None
        tilt_ok = (tilt is not None and tilt >= cfg.FALL_TILT_MIN_DEG) or exempt
        high = self._impacted and self._ff_ms > 0.0 and tilt_ok
        return self._emit_fall(now, confidence="high" if high else "low")

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
        onset = self._onset_t if self._onset_t is not None else now
        allow, worn_at, frac, reason = self._wear_gate(onset)
        lookback = getattr(cfg, "WEAR_GATE_LOOKBACK_S", 3.0)
        det = Detection(
            type="fall",
            peak_accel_g=self._peak,
            freefall_ms=self._ff_ms,
            confidence=confidence,
            imu_trace=self._trace_relative(now),
            helmet_worn_at_onset=worn_at,
            helmet_worn_lookback_s=lookback,
            wear_on_fraction=frac,
            suppressed=not allow,
            suppress_reason=reason if not allow else "",
        )
        if not allow:
            self.suppressed_falls += 1
        # One fall = one event: disarm until the signal is quiet again.
        self._cooldown_until = now + cfg.IMU_COOLDOWN_S
        self._armed = False
        self._quiet_start = None
        self._reset_state()
        return det

    def _reset_state(self) -> None:
        self._state = "idle"
        self._ff_start = None
        self._ff_ms = 0.0
        self._impact_t = None
        self._impacted = False
        self._onset_t = None
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
