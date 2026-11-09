"""Pololu MinIMU-9 v3 driver (smbus2): L3GD20H gyro + LSM303D accelerometer.

Replaces the MPU-6050 driver (archived in `hat/archive/mpu6050.py`) with the same
public interface: `read(t) -> ImuSample` (g, deg/s), `check_config()`, `who_am_i()`,
`close()`, `config_losses`, `last_recovery_t`, `i2c_errors`.

Two chips on one I2C bus (bus 3), 7-bit addresses from the Pololu page:
  L3GD20H  gyro        0x6B   WHO_AM_I (0x0F) = 0xD7
  LSM303D  accel + mag 0x1D   WHO_AM_I (0x0F) = 0x49   (magnetometer is powered down)

Facts below marked [DS] are from the ST datasheets (L3GD20H Rev 2, LSM303D Rev 2):
  - Both: sub-address bit 7 (0x80) = auto-increment on multi-byte transfers.
  - Both: data registers are little-endian (OUT_X_L at the lower address); the
    gyro's BLE bit (CTRL4 bit 6) = 0 keeps it that way. Unlike the MPU (big-endian).
  - Both: OUT_X_L = 0x28 .. OUT_Z_H = 0x2D, STATUS = 0x27 (bit 3 = new XYZ data).
  - Both: BDU (block data update) makes the output registers hold until both bytes
    of an axis are read, so a burst never mixes the halves of two samples.
  - LSM303D +-16 g = 0.732 mg/LSB; L3GD20H +-500 dps = 17.5 mdps/LSB.
  - Axes: gyro is positive for counter-clockwise rotation about +X/+Y/+Z
    (right-hand rule); a still, flat LSM303D reads X=0, Y=0, Z=+1 g. The Pololu
    page says the two chips' axes are aligned on the board (not re-verified here).

Filter / rate choice (see the comments on the register values below): host polls at
IMU_SAMPLE_HZ (200 Hz); both chips run at a 400 Hz output rate so every poll gets a
fresh sample, with the accel anti-alias filter at 50 Hz and the gyro low-pass at
~50 Hz. That keeps the signal bandwidth near the old MPU DLPF (~44 Hz) that the fall
thresholds were tuned against, and below the 100 Hz Nyquist of the 200 Hz poll.

Gyro zero-rate: gx/gy/gz are None until a quiet ~3 s window has been seen (startup
calibration, see `_calibrate`); the window's mean is then subtracted. Accel and fall
detection run from the first sample. The bias survives a CONFIG LOST re-init.

Cost per sample: 2 I2C transactions (one 6-byte burst per chip). Per config check:
2 transactions (one burst per chip). Nothing else is read in steady state.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from smbus2 import SMBus

from hat import config as cfg
from hat.imu import ImuSample

AUTO_INC = 0x80  # sub-address bit 7: auto-increment register address [DS, both chips]

# Same register numbers on both chips
WHO_AM_I = 0x0F
STATUS = 0x27          # STATUS (gyro) / STATUS_A (accel); bit 3 = ZYXDA
OUT_X_L = 0x28         # 6 bytes: XL XH YL YH ZL ZH, little-endian
STATUS_ZYXDA = 0x08

# --- L3GD20H gyro registers [DS] ---
G_CTRL1 = 0x20  # DR1 DR0 BW1 BW0 PD Zen Yen Xen
G_CTRL2 = 0x21  # EXTRen LVLen HPM1 HPM0 HPCF3..0
G_CTRL3 = 0x22  # interrupt routing
G_CTRL4 = 0x23  # BDU BLE FS1 FS0 IMPen ST2 ST1 SIM
G_CTRL5 = 0x24  # BOOT FIFO_EN StopOnFTH HPen IG_Sel1..0 Out_Sel1..0
# DR=10, BW=10: ODR 400 Hz (datasheet nominal; real 378.8 Hz, spec +-15 %),
# LPF cut-off 50 Hz; PD=1 normal mode; X/Y/Z enabled.
G_CTRL1_VAL = 0xAF
G_CTRL2_VAL = 0x00  # high-pass filter mode normal, off (HPen=0 in CTRL5)
G_CTRL3_VAL = 0x00  # no interrupts
# BDU=1, BLE=0 (LSB first), FS=01 -> +-500 dps (17.5 mdps/LSB)
G_CTRL4_VAL = 0x90
G_CTRL5_VAL = 0x00  # no FIFO, no HPF, Out_Sel=00 (LPF1 output, the DR/BW filter)

# --- LSM303D accel/mag registers [DS] ---
A_CTRL0 = 0x1F  # BOOT FIFO_EN FTH_EN 0 0 HP_Click HPIS1 HPIS2
A_CTRL1 = 0x20  # AODR3..0 BDU AZEN AYEN AXEN
A_CTRL2 = 0x21  # ABW1 ABW0 AFS2 AFS1 AFS0 0 AST SIM
A_CTRL3 = 0x22  # INT1 routing
A_CTRL4 = 0x23  # INT2 routing
A_CTRL7 = 0x26  # AHPM1..0 AFDS T_ONLY 0 MLP MD1 MD0
A_CTRL0_VAL = 0x00  # FIFO off, high-pass filters off
# AODR=1000 (400 Hz), BDU=1, X/Y/Z enabled
A_CTRL1_VAL = 0x8F
# ABW=11 (50 Hz anti-alias), AFS=100 (+-16 g), no self-test, 4-wire SPI bits unused
A_CTRL2_VAL = 0xE0
A_CTRL3_VAL = 0x00
A_CTRL4_VAL = 0x00
# MD=10: magnetometer power-down (we only use the accelerometer); no accel HPF
A_CTRL7_VAL = 0x02

# Config read-back: one burst per chip. (reg, expected, mask) in each block.
_G_BLOCK = (G_CTRL1, 5)    # 0x20..0x24
_A_BLOCK = (A_CTRL0, 8)    # 0x1F..0x26
_G_EXPECT: Dict[int, Tuple[int, int]] = {
    G_CTRL1: (G_CTRL1_VAL, 0xFF),
    G_CTRL2: (G_CTRL2_VAL, 0xFF),
    G_CTRL3: (G_CTRL3_VAL, 0xFF),
    G_CTRL4: (G_CTRL4_VAL, 0xFF),
    G_CTRL5: (G_CTRL5_VAL, 0xFF),
}
_A_EXPECT: Dict[int, Tuple[int, int]] = {
    A_CTRL0: (A_CTRL0_VAL, 0xFF),
    A_CTRL1: (A_CTRL1_VAL, 0xFF),
    A_CTRL2: (A_CTRL2_VAL, 0xFF),
    A_CTRL3: (A_CTRL3_VAL, 0xFF),
    A_CTRL4: (A_CTRL4_VAL, 0xFF),
    A_CTRL7: (A_CTRL7_VAL, 0xFF),
}


class ImuIdentityError(RuntimeError):
    """A chip answered at the expected address with the wrong WHO_AM_I.

    Raised at start-up only: scaling the data of an unknown chip with this
    driver's constants would silently produce wrong g / dps values.
    """


@dataclass(frozen=True)
class WhoAmI:
    gyro: int
    accel: int

    @property
    def ok(self) -> bool:
        return self.gyro == cfg.L3GD20H_WHO_AM_I and self.accel == cfg.LSM303D_WHO_AM_I

    def __str__(self) -> str:
        return (
            f"gyro(L3GD20H @0x{cfg.IMU_GYRO_ADDR:02X})=0x{self.gyro:02X} "
            f"(expect 0x{cfg.L3GD20H_WHO_AM_I:02X}), "
            f"accel(LSM303D @0x{cfg.IMU_ACCEL_ADDR:02X})=0x{self.accel:02X} "
            f"(expect 0x{cfg.LSM303D_WHO_AM_I:02X})"
        )


def _s16le(lo: int, hi: int) -> int:
    v = (hi << 8) | lo
    return v - 0x10000 if v & 0x8000 else v


class MiniIMU9:
    """Raw smbus2 wrapper: accel +-16 g, gyro +-500 dps, 400 Hz internal ODR."""

    def __init__(
        self,
        bus_id: int = cfg.I2C_BUS,
        gyro_addr: int = cfg.IMU_GYRO_ADDR,
        accel_addr: int = cfg.IMU_ACCEL_ADDR,
        gyro_bias: Optional[Tuple[float, float, float]] = None,
    ):
        self.bus_id = bus_id
        self.gyro_addr = gyro_addr
        self.accel_addr = accel_addr
        self.bus = SMBus(bus_id)
        self.i2c_errors = 0
        self.config_losses = 0
        self.last_recovery_t = 0.0
        # Gyro zero-rate bias (deg/s). None = not calibrated yet: read() reports gx/gy/gz as
        # None until a quiet window has been seen. Pass a tuple to skip calibration.
        self.gyro_bias: Optional[Tuple[float, float, float]] = gyro_bias
        self._cal: deque = deque()   # (t, gx, gy, gz, |a|) of the rolling window
        self._cal_n = 0
        self._cal_last_reject_log = -1e9
        ids = self.who_am_i()  # OSError here = chip missing / bus dead
        if not ids.ok:
            self.close()
            raise ImuIdentityError(f"unexpected MinIMU-9 chip ids: {ids}")
        self._setup()

    # -- bus plumbing (retry + reopen, same policy as the old driver) -----
    def _reopen(self) -> None:
        try:
            self.bus.close()
        except OSError:
            pass
        time.sleep(0.02)
        self.bus = SMBus(self.bus_id)

    def _w(self, addr: int, reg: int, val: int, retries: int = 8) -> None:
        last: Optional[OSError] = None
        for i in range(retries):
            try:
                self.bus.write_byte_data(addr, reg, val & 0xFF)
                return
            except OSError as e:
                last = e
                self.i2c_errors += 1
                time.sleep(0.002 * (i + 1))
                if i == retries // 2:
                    self._reopen()
        assert last is not None
        raise last

    def _r(self, addr: int, reg: int, retries: int = 8) -> int:
        last: Optional[OSError] = None
        for i in range(retries):
            try:
                return self.bus.read_byte_data(addr, reg)
            except OSError as e:
                last = e
                self.i2c_errors += 1
                time.sleep(0.002 * (i + 1))
                if i == retries // 2:
                    self._reopen()
        assert last is not None
        raise last

    def _read_regs(self, addr: int, reg: int, n: int, retries: int = 6) -> List[int]:
        """One burst of `n` bytes from `reg` (auto-increment bit set)."""
        last: Optional[OSError] = None
        for i in range(retries):
            try:
                return list(self.bus.read_i2c_block_data(addr, reg | AUTO_INC, n))
            except OSError as e:
                last = e
                self.i2c_errors += 1
                time.sleep(0.002 * (i + 1))
                if i == retries // 2:
                    self._reopen()
        assert last is not None
        raise last

    def _read_xyz(self, addr: int) -> Tuple[int, int, int]:
        """Raw signed X, Y, Z from OUT_X_L..OUT_Z_H in one 6-byte burst.

        Falls back to single-register reads (low byte first, as BDU requires)
        if the bus keeps refusing block transfers.
        """
        last: Optional[OSError] = None
        raw: Optional[List[int]] = None
        for i in range(6):
            try:
                raw = list(self.bus.read_i2c_block_data(addr, OUT_X_L | AUTO_INC, 6))
                break
            except OSError as e:
                last = e
                self.i2c_errors += 1
                time.sleep(0.002 * (i + 1))
                if i == 2:
                    self._reopen()
        if raw is None:
            raw = [self._r(addr, OUT_X_L + k) for k in range(6)]
        if len(raw) != 6:
            raise last or OSError(5, "short read from IMU")
        return _s16le(raw[0], raw[1]), _s16le(raw[2], raw[3]), _s16le(raw[4], raw[5])

    # -- configuration ---------------------------------------------------
    def _setup_gyro(self) -> None:
        a = self.gyro_addr
        self._w(a, G_CTRL2, G_CTRL2_VAL)
        self._w(a, G_CTRL3, G_CTRL3_VAL)
        self._w(a, G_CTRL4, G_CTRL4_VAL)
        self._w(a, G_CTRL5, G_CTRL5_VAL)
        self._w(a, G_CTRL1, G_CTRL1_VAL)  # last: leaves power-down

    def _setup_accel(self) -> None:
        a = self.accel_addr
        self._w(a, A_CTRL0, A_CTRL0_VAL)
        self._w(a, A_CTRL2, A_CTRL2_VAL)
        self._w(a, A_CTRL3, A_CTRL3_VAL)
        self._w(a, A_CTRL4, A_CTRL4_VAL)
        self._w(a, A_CTRL7, A_CTRL7_VAL)
        self._w(a, A_CTRL1, A_CTRL1_VAL)  # last: leaves power-down

    def _setup(self, gyro: bool = True, accel: bool = True) -> None:
        if gyro:
            self._setup_gyro()
        if accel:
            self._setup_accel()
        time.sleep(0.1)  # L3GD20H turn-on time is 50 ms typ. [DS]; stay clear of it
        # Do not hand out the all-zero output registers of a chip that has not
        # converted yet (|a| = 0 would look like free fall).
        for addr, want in ((self.gyro_addr, gyro), (self.accel_addr, accel)):
            if not want:
                continue
            for _ in range(20):
                if self._r(addr, STATUS) & STATUS_ZYXDA:
                    break
                time.sleep(0.005)

    def _check_block(self, addr: int, block: Tuple[int, int], expect: Dict[int, Tuple[int, int]], tag: str) -> List[str]:
        first, n = block
        data = self._read_regs(addr, first, n)
        bad = []
        for reg, (want, mask) in expect.items():
            got = data[reg - first]
            if (got & mask) != (want & mask):
                bad.append(f"{tag} 0x{reg:02X}=0x{got:02X}(want 0x{want:02X})")
        return bad

    def check_config(self) -> bool:
        """Read back the config registers of BOTH chips (2 I2C transactions).

        Returns True if everything is as `_setup` left it. On a mismatch on either
        chip (power loss / brown-out / I2C glitch: the chip falls back to its
        defaults, e.g. accel +-2 g, which makes every reading 8x too large at
        our scaling) it re-runs setup for the affected chip(s), logs loudly and
        returns False so the caller can discard the samples taken while the
        config was wrong. Raises OSError only if the bus itself is unusable.
        """
        bad_g = self._check_block(self.gyro_addr, _G_BLOCK, _G_EXPECT, "gyro")
        bad_a = self._check_block(self.accel_addr, _A_BLOCK, _A_EXPECT, "accel")
        bad = bad_g + bad_a
        if not bad:
            return True
        self.config_losses += 1
        print(
            f"[imu] CONFIG LOST ({', '.join(bad)}) — re-initialising MinIMU-9 "
            f"(loss #{self.config_losses}); recent samples are discarded",
            flush=True,
        )
        if bad_g and self.gyro_bias is None:
            self._cal.clear()  # window was taken with a wrong gyro range: unusable
        self._setup(gyro=bool(bad_g), accel=bool(bad_a))
        # A calibrated bias is kept across a re-init: same chip, same zero-rate level.
        self.last_recovery_t = time.perf_counter()
        still = (
            self._check_block(self.gyro_addr, _G_BLOCK, _G_EXPECT, "gyro")
            + self._check_block(self.accel_addr, _A_BLOCK, _A_EXPECT, "accel")
        )
        if still:
            print(f"[imu] re-init did NOT stick: {still}", flush=True)
        else:
            print("[imu] config recovered OK", flush=True)
        return False

    def who_am_i(self) -> WhoAmI:
        return WhoAmI(
            gyro=self._r(self.gyro_addr, WHO_AM_I),
            accel=self._r(self.accel_addr, WHO_AM_I),
        )

    # -- data -------------------------------------------------------------
    def read(self, t: Optional[float] = None) -> ImuSample:
        ax, ay, az = self._read_xyz(self.accel_addr)
        gx, gy, gz = self._read_xyz(self.gyro_addr)
        if t is None:
            t = time.perf_counter()
        ax, ay, az = ax * cfg.ACCEL_G_PER_LSB, ay * cfg.ACCEL_G_PER_LSB, az * cfg.ACCEL_G_PER_LSB
        gx, gy, gz = gx * cfg.GYRO_DPS_PER_LSB, gy * cfg.GYRO_DPS_PER_LSB, gz * cfg.GYRO_DPS_PER_LSB
        if self.gyro_bias is None:
            self._calibrate(t, gx, gy, gz, math.sqrt(ax * ax + ay * ay + az * az))
        bias = self.gyro_bias
        if bias is None:
            return ImuSample(t, ax, ay, az, None, None, None)
        return ImuSample(t, ax, ay, az, gx - bias[0], gy - bias[1], gz - bias[2])

    # -- startup gyro zero-rate calibration ------------------------------
    def _calibrate(self, t: float, gx: float, gy: float, gz: float, amag: float) -> None:
        """Feed one sample to the rolling quiet-window search (no I2C, no blocking).

        Every 50 samples (~0.25 s) the last GYRO_CAL_WINDOW_S seconds are tested:
        per-axis std <= GYRO_CAL_MAX_STD_DPS, the two half-window means agree,
        |a| steady and plausible. The first passing window's mean is the bias,
        unless it exceeds the datasheet zero-rate level (then it is rejected, logged,
        and the search goes on). Orientation is never looked at.
        """
        w = self._cal
        w.append((t, gx, gy, gz, amag))
        while w and t - w[0][0] > cfg.GYRO_CAL_WINDOW_S:
            w.popleft()
        self._cal_n += 1
        if self._cal_n % 50:
            return
        n = len(w)
        if (
            t - w[0][0] < 0.95 * cfg.GYRO_CAL_WINDOW_S
            or n < 0.5 * cfg.GYRO_CAL_WINDOW_S * cfg.IMU_SAMPLE_HZ
        ):
            return
        cols = list(zip(*w))[1:]  # gx, gy, gz, |a|
        half = n // 2
        means = []
        for k in range(3):
            c = cols[k]
            m = sum(c) / n
            if math.sqrt(sum((v - m) ** 2 for v in c) / n) > cfg.GYRO_CAL_MAX_STD_DPS:
                return
            if abs(sum(c[:half]) / half - sum(c[half:]) / (n - half)) > cfg.GYRO_CAL_MAX_HALF_DIFF_DPS:
                return
            means.append(m)
        a = cols[3]
        am = sum(a) / n
        if not 0.7 <= am <= 1.3 or math.sqrt(sum((v - am) ** 2 for v in a) / n) > cfg.GYRO_CAL_MAX_ACC_STD_G:
            return
        if max(abs(m) for m in means) > cfg.GYRO_CAL_MAX_BIAS_DPS:
            if t - self._cal_last_reject_log > 30.0:
                self._cal_last_reject_log = t
                print(
                    f"[imu] gyro zero-rate rejected: still window mean ({means[0]:+.1f}, {means[1]:+.1f}, "
                    f"{means[2]:+.1f}) dps exceeds {cfg.GYRO_CAL_MAX_BIAS_DPS:.0f} dps; gyro stays off",
                    flush=True,
                )
            return
        self.gyro_bias = (means[0], means[1], means[2])
        w.clear()
        print(
            f"[imu] gyro bias calibrated: ({means[0]:+.2f}, {means[1]:+.2f}, {means[2]:+.2f}) dps "
            f"from {n} samples; gyro output enabled",
            flush=True,
        )

    def close(self) -> None:
        try:
            self.bus.close()
        except OSError:
            pass
