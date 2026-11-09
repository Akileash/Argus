"""Tests for the ARCHIVED MPU-6050 driver (hat/archive/mpu6050.py).

Moved unchanged from test_imu_config_and_fall.py when the IMU was swapped for the
MinIMU-9 (2026-11-08); only the imports differ. They keep the archive importable
and working in case someone switches back to an MPU.
"""

from __future__ import annotations

import pytest

from hat.archive import mpu6050 as imu_mod
from hat.archive.mpu6050 import (
    ACCEL_CONFIG, ACCEL_CONFIG2, CONFIG, GYRO_CONFIG, PWR_MGMT_1, SMPLRT_DIV,
    WHO_AM_I, MPU6050,
)

# --------------------------------------------------------------------------
# Mock bus
# --------------------------------------------------------------------------
class FakeBus:
    def __init__(self, who=0x70):
        self.regs = {PWR_MGMT_1: 0x40, WHO_AM_I: who}
        self.transactions = 0
        self.fail_next = 0

    def _maybe_fail(self):
        if self.fail_next:
            self.fail_next -= 1
            raise OSError(121, "Remote I/O error")

    def write_byte_data(self, addr, reg, val):
        self._maybe_fail()
        self.regs[reg] = val

    def read_byte_data(self, addr, reg):
        self._maybe_fail()
        self.transactions += 1
        return self.regs.get(reg, 0)

    def read_i2c_block_data(self, addr, reg, n):
        self._maybe_fail()
        self.transactions += 1
        return [self.regs.get(reg + i, 0) for i in range(n)]

    def close(self):
        pass

    def chip_reset(self):
        """What a brown-out does to an MPU-6500: awake, all-default (+-2 g)."""
        for r in (SMPLRT_DIV, CONFIG, GYRO_CONFIG, ACCEL_CONFIG, ACCEL_CONFIG2):
            self.regs[r] = 0x00
        self.regs[PWR_MGMT_1] = 0x01


@pytest.fixture
def mpu(monkeypatch):
    bus = FakeBus()
    monkeypatch.setattr(imu_mod, "SMBus", lambda _id: bus)
    monkeypatch.setattr(imu_mod.time, "sleep", lambda _s: None)
    m = MPU6050()
    return m, bus


def test_setup_configures_6500_family(mpu):
    m, bus = mpu
    assert bus.regs[ACCEL_CONFIG] == 0x18 and bus.regs[GYRO_CONFIG] == 0x10
    assert bus.regs[ACCEL_CONFIG2] == 0x03  # accel DLPF only exists on 6500 family
    assert m.check_config() is True


def test_true_mpu6050_does_not_touch_accel_config2(monkeypatch):
    bus = FakeBus(who=0x68)
    monkeypatch.setattr(imu_mod, "SMBus", lambda _id: bus)
    monkeypatch.setattr(imu_mod.time, "sleep", lambda _s: None)
    m = MPU6050()
    assert ACCEL_CONFIG2 not in bus.regs
    assert m.check_config() is True


def test_check_config_is_cheap(mpu):
    m, bus = mpu
    bus.transactions = 0
    m.check_config()
    assert bus.transactions <= 2  # one block read + PWR_MGMT_1


def test_config_loss_detected_and_recovered(mpu, capsys):
    m, bus = mpu
    bus.chip_reset()
    assert m.check_config() is False          # loss detected, re-initialised
    assert bus.regs[ACCEL_CONFIG] == 0x18 and bus.regs[GYRO_CONFIG] == 0x10
    assert m.config_losses == 1
    out = capsys.readouterr().out
    assert "CONFIG LOST" in out and "recovered OK" in out
    assert m.check_config() is True           # stays fixed


def test_sleep_bit_detected(mpu):
    m, bus = mpu
    bus.regs[PWR_MGMT_1] = 0x40
    assert m.check_config() is False
    assert bus.regs[PWR_MGMT_1] == 0x00


def test_transient_i2c_error_does_not_crash_check(mpu):
    m, bus = mpu
    bus.fail_next = 2
    assert m.check_config() is True
    assert m.i2c_errors >= 2


def test_dead_bus_raises_oserror_for_caller_to_handle(mpu):
    m, bus = mpu
    bus.fail_next = 10_000
    with pytest.raises(OSError):
        m.check_config()
