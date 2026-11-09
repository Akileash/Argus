"""ARCHIVED: MPU-6050 / MPU-6500-family driver (smbus2).

Why this is here: the MPU chip on the hat was damaged on 2026-11-08 and the
IMU was replaced by a Pololu MinIMU-9 v3 (L3GD20H gyro + LSM303D accel), see
`hat/minimu9.py`. This file is the previous driver, moved here with its register
constants. The class body is byte-for-byte what `hat/imu.py` had, except that the
four values it used to read from `hat.config` are now module constants below
(they were removed from `hat.config` when the MiniIMU-9 took over; values
unchanged).

How to switch back to an MPU (needs a working MPU-6050/6500 wired to I2C bus 3,
address 0x68):
  1. In `hat/main.py`, `hat/tools/test_imu.py`, `hat/tools/test_imu_clip.py` and
     `pi_bringup/test_all_at_once.py` replace
         from hat.minimu9 import MiniIMU9
     with
         from hat.archive.mpu6050 import MPU6050 as MiniIMU9
     (the public interface is the same: read(t) -> ImuSample, check_config(),
     who_am_i(), close(), config_losses, last_recovery_t, i2c_errors).
     `who_am_i()` here returns one int (0x68 or 0x70); the MiniIMU-9 returns a
     (gyro_id, accel_id) tuple, so the `print` in those callers needs the old form.
  2. Nothing in `hat.config` needs restoring: the constants it needs are below.
  3. The fall thresholds in `hat/config.py` were tuned against this chip's
     ~44 Hz DLPF at +-16 g; the MiniIMU-9 settings were chosen to match that.
  4. Tests for this driver: `hat/tests/test_archive_mpu6050.py`.
"""

from __future__ import annotations

import struct
import time
from typing import List, Optional

from smbus2 import SMBus

from hat import config as cfg
from hat.imu import ImuSample

# Values that lived in hat.config while this was the live driver.
MPU_ADDR = 0x68
ACCEL_LSB_PER_G = 2048.0        # +-16 g
GYRO_LSB_PER_DPS = 32.8         # +-1000 dps
MPU6500_FAMILY_IDS = (0x70, 0x71, 0x73)   # WHO_AM_I of MPU-6500/9250/9255

# Register map
PWR_MGMT_1 = 0x6B
SMPLRT_DIV = 0x19
CONFIG = 0x1A
GYRO_CONFIG = 0x1B
ACCEL_CONFIG = 0x1C
ACCEL_CONFIG2 = 0x1D  # MPU-6500 family only (accel DLPF)
ACCEL_XOUT_H = 0x3B
WHO_AM_I = 0x75


class MPU6050:
    """Raw smbus2 wrapper: ±16 g, ±1000 dps, DLPF ~44 Hz."""

    def __init__(self, bus_id: int = cfg.I2C_BUS, addr: int = MPU_ADDR):
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
        if self.who_am_i() in MPU6500_FAMILY_IDS:
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
            ax=ax / ACCEL_LSB_PER_G,
            ay=ay / ACCEL_LSB_PER_G,
            az=az / ACCEL_LSB_PER_G,
            gx=gx / GYRO_LSB_PER_DPS,
            gy=gy / GYRO_LSB_PER_DPS,
            gz=gz / GYRO_LSB_PER_DPS,
        )

    def close(self) -> None:
        try:
            self.bus.close()
        except OSError:
            pass


