"""MinIMU-9 (L3GD20H + LSM303D) driver tests against a mock I2C bus with two register maps.

The mock follows the ST datasheets (register addresses, reset values, little-endian
output, sub-address bit 7 = auto-increment, full-scale dependent sensitivity), so a
driver that forgets the auto-increment bit, mixes up byte order, or leaves a chip in
its reset range fails here. What this CANNOT show: that the real chips behave like
the datasheet, that the axes are mounted as expected, real timing/noise/filter
response, or the Pi's I2C controller quirks. See `python -m hat.tools.test_imu check`.
"""

from __future__ import annotations

import math
import random
import struct

import pytest

from hat import config as cfg
from hat import minimu9 as mm
from hat.imu import FallImpactDetector, ImuSample
from hat.minimu9 import ImuIdentityError, MiniIMU9, WhoAmI
from hat.ultrasonic import MotionState
from hat.wear_state import WearState

GYRO = cfg.IMU_GYRO_ADDR
ACCEL = cfg.IMU_ACCEL_ADDR

# datasheet sensitivities (typ.), g/LSB and dps/LSB, keyed by the FS field
ACCEL_G_PER_LSB_BY_AFS = {0: 0.000061, 1: 0.000122, 2: 0.000183, 3: 0.000244, 4: 0.000732}
GYRO_DPS_PER_LSB_BY_FS = {0: 0.00875, 1: 0.0175, 2: 0.070, 3: 0.070}
ZERO = (0.0, 0.0, 0.0)


def _clamp16(v: int) -> int:
    return max(-32768, min(32767, v))


class FakeMiniBus:
    """Two chips on one bus. Registers 0x00..0x3F per address."""

    def __init__(self, who_gyro=0xD7, who_accel=0x49):
        self.regs = {GYRO: [0] * 0x40, ACCEL: [0] * 0x40}
        self.who = {GYRO: who_gyro, ACCEL: who_accel}
        self.writes = []            # (addr, reg, val) in order
        self.reads = []             # (addr, sub_address_as_sent, n)
        self.transactions = {GYRO: 0, ACCEL: 0}
        self.fail_next = 0
        self.fail_addr = None       # only this address fails (chip missing)
        self.block_fails = False    # bus refuses block transfers
        self.stuck = {GYRO: False, ACCEL: False}   # chip ignores writes
        self.closed = 0
        self.power_loss(GYRO)
        self.power_loss(ACCEL)

    # -- chip state ----------------------------------------------------
    def power_loss(self, addr):
        """Chip back to its reset values (datasheet register maps)."""
        r = self.regs[addr]
        for i in range(0x40):
            r[i] = 0
        r[0x0F] = self.who[addr]
        r[0x20] = 0x07
        if addr == ACCEL:
            r[0x24] = 0x18
            r[0x25] = 0x20
            r[0x26] = 0x01
        r[0x27] = 0x0F  # new XYZ data available

    def accel_scale(self):
        return ACCEL_G_PER_LSB_BY_AFS[min((self.regs[ACCEL][0x21] >> 3) & 0x07, 4)]

    def gyro_scale(self):
        return GYRO_DPS_PER_LSB_BY_FS[(self.regs[GYRO][0x23] >> 4) & 0x03]

    def feed(self, a=(0.0, 0.0, 1.0), g=(0.0, 0.0, 0.0)):
        """Physical input -> output registers, using the chips' CURRENT range."""
        sa, sg = self.accel_scale(), self.gyro_scale()
        self.feed_raw(
            [_clamp16(round(v / sa)) for v in a],
            [_clamp16(round(v / sg)) for v in g],
        )

    def feed_raw(self, araw, graw):
        self.regs[ACCEL][0x28:0x2E] = list(struct.pack("<hhh", *araw))
        self.regs[GYRO][0x28:0x2E] = list(struct.pack("<hhh", *graw))

    # -- smbus2 API ----------------------------------------------------
    def _maybe_fail(self, addr):
        if self.fail_addr is not None and addr == self.fail_addr:
            raise OSError(121, "Remote I/O error")
        if self.fail_next:
            self.fail_next -= 1
            raise OSError(121, "Remote I/O error")

    def write_byte_data(self, addr, reg, val):
        self._maybe_fail(addr)
        self.writes.append((addr, reg, val))
        if not self.stuck[addr]:
            self.regs[addr][reg] = val

    def read_byte_data(self, addr, reg):
        self._maybe_fail(addr)
        self.transactions[addr] += 1
        return self.regs[addr][reg & 0x7F]

    def read_i2c_block_data(self, addr, reg, n):
        self._maybe_fail(addr)
        if self.block_fails:
            raise OSError(5, "block transfer refused")
        self.transactions[addr] += 1
        self.reads.append((addr, reg, n))
        base = reg & 0x7F
        if reg & 0x80:  # auto-increment
            return [self.regs[addr][base + i] for i in range(n)]
        return [self.regs[addr][base]] * n  # real chips re-read the same register

    def close(self):
        self.closed += 1


@pytest.fixture
def rig(monkeypatch):
    bus = FakeMiniBus()
    opened = []

    def factory(_bus_id):
        opened.append(1)
        return bus

    monkeypatch.setattr(mm, "SMBus", factory)
    monkeypatch.setattr(mm.time, "sleep", lambda _s: None)
    imu = MiniIMU9(gyro_bias=ZERO)   # calibration is tested separately
    bus.feed()
    return imu, bus, opened


def _reset_counters(bus):
    bus.transactions = {GYRO: 0, ACCEL: 0}
    bus.reads.clear()
    bus.writes.clear()


# --------------------------------------------------------------------------
# init, identity
# --------------------------------------------------------------------------
def test_init_sequence_registers_and_order(rig):
    imu, bus, _ = rig
    g, a = bus.regs[GYRO], bus.regs[ACCEL]
    # gyro: CTRL1 ODR 400 Hz / 50 Hz cut-off, normal mode, XYZ on; CTRL4 BDU + 500 dps
    assert [g[r] for r in range(0x20, 0x25)] == [0xAF, 0x00, 0x00, 0x90, 0x00]
    assert g[0x23] & 0x80 and (g[0x23] >> 4) & 3 == 1 and not g[0x23] & 0x40  # BDU, FS=500, BLE=0
    # accel: CTRL0 clean, CTRL1 400 Hz + BDU + XYZ, CTRL2 50 Hz AA + +-16 g, CTRL7 mag power-down
    assert [a[r] for r in (0x1F, 0x20, 0x21, 0x22, 0x23, 0x26)] == [0x00, 0x8F, 0x20, 0x00, 0x00, 0x02]
    assert a[0x20] >> 4 == 0b1000 and a[0x20] & 0x08 and a[0x20] & 0x07 == 7
    assert (a[0x21] >> 6) == 0b00 and ((a[0x21] >> 3) & 7) == 0b100
    assert a[0x26] & 0x03 == 0b10  # MD = power-down
    # each chip is switched on (CTRL1) last
    for addr in (GYRO, ACCEL):
        regs = [r for (ad, r, _v) in bus.writes if ad == addr]
        assert regs[-1] == 0x20 and regs.count(0x20) == 1
    assert imu.check_config() is True


def test_who_am_i_returns_both_ids(rig):
    imu, _, _ = rig
    ids = imu.who_am_i()
    assert ids == WhoAmI(gyro=0xD7, accel=0x49) and ids.ok
    assert "0xD7" in str(ids) and "0x49" in str(ids)


@pytest.mark.parametrize("who_g,who_a", [(0x68, 0x49), (0xD7, 0x00), (0x70, 0x68), (0xD4, 0x49)])
def test_wrong_who_am_i_refuses_to_start(monkeypatch, who_g, who_a):
    bus = FakeMiniBus(who_gyro=who_g, who_accel=who_a)
    monkeypatch.setattr(mm, "SMBus", lambda _i: bus)
    monkeypatch.setattr(mm.time, "sleep", lambda _s: None)
    with pytest.raises(ImuIdentityError) as ei:
        MiniIMU9()
    assert f"0x{who_g:02X}" in str(ei.value)
    assert bus.writes == []     # configured nothing on an unknown chip
    assert bus.closed == 1


@pytest.mark.parametrize("missing", [GYRO, ACCEL])
def test_missing_chip_raises_oserror(monkeypatch, missing):
    bus = FakeMiniBus()
    bus.fail_addr = missing
    monkeypatch.setattr(mm, "SMBus", lambda _i: bus)
    monkeypatch.setattr(mm.time, "sleep", lambda _s: None)
    with pytest.raises(OSError):
        MiniIMU9()


def test_dead_bus_raises_oserror_everywhere(rig):
    imu, bus, _ = rig
    bus.fail_next = 10_000
    with pytest.raises(OSError):
        imu.check_config()
    with pytest.raises(OSError):
        imu.read()
    with pytest.raises(OSError):
        imu.who_am_i()


def test_transient_i2c_error_does_not_crash_check_or_read(rig):
    imu, bus, _ = rig
    bus.fail_next = 2
    assert imu.check_config() is True
    assert imu.i2c_errors >= 2
    bus.fail_next = 2
    assert imu.read(1.0).t == 1.0


def test_bus_reopened_after_repeated_errors(rig):
    imu, bus, opened = rig
    n0 = len(opened)
    bus.fail_next = 5
    imu.check_config()
    assert len(opened) > n0


# --------------------------------------------------------------------------
# scaling, signs, byte order, burst reads
# --------------------------------------------------------------------------
def test_scaling_is_exact_datasheet_values(rig):
    imu, bus, _ = rig
    bus.feed_raw([1366, -1366, 21858], [28571, -14286, 100])
    s = imu.read(5.0)
    assert (s.t, s.ax, s.ay, s.az) == (5.0, 1366 * 0.000732, -1366 * 0.000732, 21858 * 0.000732)
    assert s.ax == pytest.approx(1.0, abs=2e-4)          # 1366 LSB ~ 1 g  (0.732 mg/LSB)
    assert s.az == pytest.approx(16.0, abs=2e-3)         # 21858 LSB ~ full +16 g
    assert (s.gx, s.gy, s.gz) == (28571 * 0.0175, -14286 * 0.0175, 100 * 0.0175)
    assert s.gx == pytest.approx(500.0, abs=0.05)        # 17.5 mdps/LSB
    assert abs(2048.0 * 0.000732 - 1.0) > 0.4            # NOT the old MPU 2048 LSB/g


def test_little_endian_and_sign(rig):
    imu, bus, _ = rig
    # accel X = bytes (lo=0x01, hi=0x00) = +1 LSB; big-endian would give 256
    bus.regs[ACCEL][0x28:0x2E] = [0x01, 0x00, 0xFF, 0xFF, 0x00, 0x80]   # +1, -1, -32768
    bus.regs[GYRO][0x28:0x2E] = [0x00, 0x01, 0xFE, 0xFF, 0xFF, 0x7F]    # +256, -2, +32767
    s = imu.read()
    assert s.ax == pytest.approx(0.000732) and s.ay == pytest.approx(-0.000732)
    assert s.az == pytest.approx(-32768 * 0.000732)
    assert s.gx == pytest.approx(256 * 0.0175) and s.gy == pytest.approx(-0.035) and s.gz == pytest.approx(32767 * 0.0175)


def test_read_is_two_bursts_with_autoincrement(rig):
    imu, bus, _ = rig
    _reset_counters(bus)
    bus.feed((0.1, -0.2, 0.9), (1.0, 2.0, 3.0))
    s = imu.read()
    assert bus.transactions == {GYRO: 1, ACCEL: 1}       # I2C cost: 2 transactions per sample
    assert sorted(bus.reads) == sorted([(ACCEL, 0xA8, 6), (GYRO, 0xA8, 6)])   # 0x28 | 0x80
    assert bus.writes == []                              # steady state never writes
    assert (s.ax, s.ay, s.az) == pytest.approx((0.1, -0.2, 0.9), abs=1e-3)
    assert (s.gx, s.gy, s.gz) == pytest.approx((1.0, 2.0, 3.0), abs=0.05)


def test_read_falls_back_to_single_register_reads(rig):
    imu, bus, _ = rig
    bus.feed((0.0, 0.5, 1.0), (0.0, 0.0, 7.0))
    bus.block_fails = True
    s = imu.read()
    assert (s.ay, s.az, s.gz) == pytest.approx((0.5, 1.0, 7.0), abs=1e-3)


# --------------------------------------------------------------------------
# config loss / recovery (either chip)
# --------------------------------------------------------------------------
def test_check_config_costs_two_transactions(rig):
    imu, bus, _ = rig
    _reset_counters(bus)
    assert imu.check_config() is True
    assert bus.transactions == {GYRO: 1, ACCEL: 1}
    assert bus.writes == []


@pytest.mark.parametrize("chip,tag", [(GYRO, "gyro"), (ACCEL, "accel")])
def test_config_loss_on_one_chip_detected_and_recovered(rig, capsys, chip, tag):
    imu, bus, _ = rig
    other = ACCEL if chip == GYRO else GYRO
    bus.power_loss(chip)
    _reset_counters(bus)
    assert imu.check_config() is False
    out = capsys.readouterr().out
    assert "[imu] CONFIG LOST" in out and f"{tag} 0x20=0x07" in out
    assert "[imu] config recovered OK" in out and "did NOT stick" not in out
    assert imu.config_losses == 1 and imu.last_recovery_t > 0
    assert all(a == chip for (a, _r, _v) in bus.writes)       # healthy chip untouched
    assert bus.regs[other][0x20] in (0xAF, 0x8F)
    assert imu.check_config() is True                         # stays fixed
    assert imu.config_losses == 1


def test_config_loss_on_both_chips_listed_together(rig, capsys):
    imu, bus, _ = rig
    bus.power_loss(GYRO)
    bus.power_loss(ACCEL)
    assert imu.check_config() is False
    out = capsys.readouterr().out
    assert out.count("CONFIG LOST") == 1 and "gyro 0x" in out and "accel 0x" in out
    assert imu.check_config() is True


@pytest.mark.parametrize(
    "chip,reg,val",
    [
        (GYRO, 0x23, 0x80),     # gyro fell back to 245 dps, BDU kept
        (GYRO, 0x20, 0x07),     # gyro powered down
        (GYRO, 0x24, 0x10),     # high-pass enabled
        (ACCEL, 0x21, 0xC0),    # accel range back to +-2 g
        (ACCEL, 0x20, 0x07),    # accel power-down
        (ACCEL, 0x26, 0x00),    # magnetometer woke up
        (ACCEL, 0x1F, 0x80),    # BOOT/FIFO bits
    ],
)
def test_each_config_register_is_watched(rig, chip, reg, val):
    imu, bus, _ = rig
    bus.regs[chip][reg] = val
    assert imu.check_config() is False
    assert imu.check_config() is True


def test_recovery_that_does_not_stick_is_reported(rig, capsys):
    imu, bus, _ = rig
    bus.power_loss(ACCEL)
    bus.stuck[ACCEL] = True
    assert imu.check_config() is False
    assert "re-init did NOT stick" in capsys.readouterr().out


# --------------------------------------------------------------------------
# end to end: raw register bytes -> driver -> FallImpactDetector / MotionState
# --------------------------------------------------------------------------
DT = 0.005
UP = (0.0, 0.0, -1.0)       # same convention as the existing fall tests
SIDE = (-1.0, 0.0, 0.0)
FF = (0.0, 0.0, -0.12)


class Rig:
    def __init__(self, imu, bus, det, motion=None, scale=1.0, seed=1):
        self.imu, self.bus, self.det, self.motion = imu, bus, det, motion
        self.t = 1000.0
        self.scale = scale
        self.hits = []
        self.rng = random.Random(seed)

    def push(self, v, g=(0.0, 0.0, 0.0)):
        n = lambda: self.rng.gauss(0, 0.015)  # noqa: E731
        self.bus.feed([self.scale * (c + n()) for c in v], g)
        s = self.imu.read(self.t)
        if self.motion is not None:
            self.motion.push(s.t, s.ax, s.ay, s.az, s.gx, s.gy, s.gz)
        self.t += DT
        d = self.det.push(s)
        if d is not None:
            self.hits.append(d)

    def hold(self, v, secs, g=(0.0, 0.0, 0.0)):
        for _ in range(int(secs / DT)):
            self.push(v, g)

    def ramp(self, a, b, secs):
        n = max(1, int(secs / DT))
        for i in range(n):
            f = i / n
            self.push(tuple(x + (y - x) * f for x, y in zip(a, b)))

    def real_fall(self):
        self.hold(UP, 1.2)
        self.ramp(UP, FF, 0.04)
        self.hold(FF, 0.12)
        self.hold((0.0, 0.0, -3.4), 0.03)
        self.ramp((1.0, 0.5, -2.0), SIDE, 0.15)
        self.hold(SIDE, 1.0)


def test_real_fall_through_the_driver_fires_once(rig):
    imu, bus, _ = rig
    r = Rig(imu, bus, FallImpactDetector())
    r.real_fall()
    r.hold(SIDE, 8.0)
    assert len(r.hits) == 1
    h = r.hits[0]
    assert h.type == "fall" and h.confidence == "high" and h.peak_accel_g >= 3.0 and h.freefall_ms >= 100
    assert h.imu_trace and len(h.imu_trace[0]) == 4        # imu_trace format unchanged


def test_wear_gate_suppresses_through_the_driver(rig):
    imu, bus, _ = rig
    wear = WearState(history_s=30.0)
    for i in range(60):
        wear.update(False, t=1000.0 + i * 0.05)
    r = Rig(imu, bus, FallImpactDetector(wear_state=wear))
    r.t = 1003.0
    r.real_fall()
    assert len(r.hits) == 1 and r.hits[0].suppressed


def test_8x_style_wrong_scale_never_fires(rig):
    imu, bus, _ = rig
    det = FallImpactDetector()
    r = Rig(imu, bus, det, scale=8.0)       # every reading 8x too big (the old MPU failure mode)
    r.hold(UP, 3.0)
    assert det.implausible
    r.real_fall()
    r.hold(SIDE, 5.0)
    assert r.hits == []


def test_chip_reset_to_2g_range_is_12x_and_blocked_until_repaired(rig):
    imu, bus, _ = rig
    det = FallImpactDetector()
    r = Rig(imu, bus, det)
    r.hold(UP, 1.0)
    bus.power_loss(ACCEL)                   # brown-out: back to +-2 g, accel in power-down defaults
    bus.feed((0.0, 0.0, -1.0), (0, 0, 0))   # output now encoded with 0.061 mg/LSB
    assert 11.5 < imu.read().mag < 12.5     # 0.732/0.061 = 12x: not 8x on this chip
    r.hold(UP, 2.0)                         # driver not yet checked: samples are garbage
    assert det.implausible and r.hits == []
    assert imu.check_config() is False      # periodic check finds and repairs it
    det.config_lost(r.t)
    r.hold(UP, 3.0)
    assert not det.implausible
    r.real_fall()
    assert len(r.hits) == 1


def test_config_lost_mid_fall_discards_event_via_driver(rig):
    imu, bus, _ = rig
    det = FallImpactDetector()
    r = Rig(imu, bus, det)
    r.hold(UP, 1.2)
    r.ramp(UP, FF, 0.04)
    r.hold(FF, 0.12)
    r.hold((0.0, 0.0, -3.4), 0.03)
    bus.power_loss(GYRO)                    # loss on the OTHER chip also discards data
    assert imu.check_config() is False
    det.config_lost(r.t)
    r.hold(SIDE, 2.0)
    assert r.hits == [] and det.config_purges == 1


def test_motion_state_fed_from_driver(rig):
    imu, bus, _ = rig
    ms = MotionState()
    r = Rig(imu, bus, FallImpactDetector(), motion=ms)
    r.hold(UP, 1.0)
    assert ms.valid(r.t) and not ms.moving(r.t) and ms.gyro_dps < 1.0
    assert not ms.gated(r.t)
    r.hold(UP, 0.3, g=(0.0, 50.0, 0.0))    # head turning at 50 dps
    assert ms.gyro_dps == pytest.approx(50.0, abs=0.1)
    assert ms.gated(r.t) and ms.moving(r.t)
    for i in range(int(2.0 / DT)):           # walking bounce
        r.push((0.0, 0.0, -(1.0 + 0.4 * math.sin(i * 0.4))))
    assert ms.dyn_rms_g > cfg.US_MOVING_DYN_G


def test_walking_and_nodding_through_driver_are_not_falls(rig):
    imu, bus, _ = rig
    r = Rig(imu, bus, FallImpactDetector())
    for i in range(int(15 / DT)):
        ph = 2 * math.pi * 1.9 * i * DT
        r.push((0.15 * math.sin(ph / 2), 0.1, -(1.0 + 0.45 * math.sin(ph))))
    for i in range(int(8 / DT)):
        a = math.radians(35) * math.sin(2 * math.pi * 0.8 * i * DT)
        r.push((math.sin(a), 0.0, -math.cos(a)))
    assert r.hits == []


def test_imu_sample_fields_unchanged(rig):
    imu, _, _ = rig
    s = imu.read(2.0)
    assert isinstance(s, ImuSample)
    assert [f for f in ("t", "ax", "ay", "az", "gx", "gy", "gz")] == list(s.__dataclass_fields__)
    assert hasattr(imu, "config_losses") and hasattr(imu, "last_recovery_t") and hasattr(imu, "i2c_errors")


# --------------------------------------------------------------------------
# startup gyro zero-rate calibration
# --------------------------------------------------------------------------
@pytest.fixture
def uncal(monkeypatch):
    bus = FakeMiniBus()
    monkeypatch.setattr(mm, "SMBus", lambda _i: bus)
    monkeypatch.setattr(mm.time, "sleep", lambda _s: None)
    imu = MiniIMU9()
    bus.feed()
    return imu, bus


def _run(imu, bus, rig_, secs, a=(0.0, 0.0, 1.0), g=ZERO, gnoise=0.1):
    """Drive samples; returns the last sample. rig_ is a [t, rng] list."""
    s = None
    for _ in range(int(secs / DT)):
        bus.feed(
            [c + rig_[1].gauss(0, 0.01) for c in a],
            [c + rig_[1].gauss(0, gnoise) for c in g],
        )
        s = imu.read(rig_[0])
        rig_[0] += DT
    return s


def test_gyro_is_none_until_calibrated_and_accel_works_from_first_sample(uncal, capsys):
    imu, bus = uncal
    st = [1000.0, random.Random(1)]
    s = _run(imu, bus, st, 1.0)
    assert (s.gx, s.gy, s.gz) == (None, None, None)
    assert s.mag == pytest.approx(1.0, abs=0.05)
    assert imu.gyro_bias is None
    s = _run(imu, bus, st, 2.5)
    assert imu.gyro_bias is not None and s.gx is not None
    out = capsys.readouterr().out
    assert out.count("[imu] gyro bias calibrated") == 1


def test_bias_is_measured_and_subtracted_on_a_tilted_table(uncal):
    imu, bus = uncal
    st = [1000.0, random.Random(2)]
    tilted = (0.5, -0.5, math.sqrt(0.5))        # 45+ degrees off level: gravity direction is irrelevant
    s = _run(imu, bus, st, 3.6, a=tilted, g=(4.0, -7.0, 2.5))
    assert imu.gyro_bias == pytest.approx((4.0, -7.0, 2.5), abs=0.1)
    s = _run(imu, bus, st, 0.5, a=tilted, g=(4.0, -7.0, 2.5))
    assert (s.gx, s.gy, s.gz) == pytest.approx(ZERO, abs=0.5)
    s = _run(imu, bus, st, 0.2, a=tilted, g=(54.0, -7.0, 2.5), gnoise=0.0)   # a real 50 dps turn
    assert s.gx == pytest.approx(50.0, abs=0.5)


def test_noisy_start_then_quiet_calibrates_only_from_the_quiet_part(uncal):
    imu, bus = uncal
    st = [1000.0, random.Random(3)]
    # head moving for 4 s: wide gyro spread and bouncing |a|
    for i in range(int(4.0 / DT)):
        w = 40.0 * math.sin(i * 0.15)
        bus.feed((0.0, 0.0, 1.0 + 0.3 * math.sin(i * 0.2)), (w, -w, 0.5 * w))
        imu.read(st[0])
        st[0] += DT
        assert imu.gyro_bias is None
    _run(imu, bus, st, 3.6, g=(1.0, 1.0, -1.0))
    assert imu.gyro_bias == pytest.approx((1.0, 1.0, -1.0), abs=0.15)   # not polluted by the motion


def test_never_quiet_keeps_gyro_none_without_crash_and_falls_still_work(uncal):
    imu, bus = uncal
    det = FallImpactDetector()
    ms = MotionState()
    r = Rig(imu, bus, det, motion=ms)
    # constant head shaking (std ~14 dps) through the whole trace, including the fall
    shake = lambda i: (20.0 * math.sin(i * 0.3), 20.0 * math.cos(i * 0.21), 10.0 * math.sin(i * 0.1))  # noqa: E731
    seen = []
    orig_push = r.push

    def push(v, g=ZERO):
        seen.append(len(seen))
        orig_push(v, shake(len(seen)))
    r.push = push
    r.real_fall()
    r.hold(SIDE, 8.0)
    assert imu.gyro_bias is None
    assert len(r.hits) == 1 and r.hits[0].type == "fall"
    assert ms.gyro_dps == 0.0 and ms.last_t is not None     # None gyro = rotation unobservable, no crash


def test_absurd_bias_is_rejected_and_logged_once(uncal, capsys):
    imu, bus = uncal
    st = [1000.0, random.Random(4)]
    s = _run(imu, bus, st, 8.0, g=(45.0, 0.0, 0.0))        # perfectly still but reads 45 dps
    assert imu.gyro_bias is None and (s.gx, s.gy, s.gz) == (None, None, None)
    assert capsys.readouterr().out.count("gyro zero-rate rejected") == 1
    s = _run(imu, bus, st, 3.6, g=(25.0, 0.0, 0.0))        # within the datasheet +-25 dps level: accepted
    assert imu.gyro_bias == pytest.approx((25.0, 0.0, 0.0), abs=0.2)


def test_slow_steady_turn_is_not_taken_as_bias(uncal):
    imu, bus = uncal
    st = [1000.0, random.Random(5)]
    for i in range(int(6.0 / DT)):                          # ramping rate 0 -> 6 dps: halves differ by 3 dps
        bus.feed((0, 0, 1), (6.0 * (i * DT) / 6.0, 0.0, 0.0))
        imu.read(st[0])
        st[0] += DT
    assert imu.gyro_bias is None


def test_config_loss_keeps_bias(uncal, capsys):
    imu, bus = uncal
    st = [1000.0, random.Random(6)]
    _run(imu, bus, st, 3.6, g=(2.0, -1.0, 0.5))
    bias = imu.gyro_bias
    assert bias is not None
    bus.power_loss(GYRO)
    assert imu.check_config() is False
    assert imu.gyro_bias == bias                            # no new still window needed
    bus.feed((0, 0, 1), (2.0, -1.0, 0.5))
    s = imu.read(st[0])
    assert (s.gx, s.gy, s.gz) == pytest.approx(ZERO, abs=0.5)


def test_config_loss_during_calibration_discards_the_window(uncal):
    imu, bus = uncal
    st = [1000.0, random.Random(7)]
    _run(imu, bus, st, 2.0, g=(3.0, 0.0, 0.0))
    bus.power_loss(GYRO)
    imu.check_config()
    assert len(imu._cal) == 0 and imu.gyro_bias is None


def test_calibration_costs_no_extra_i2c(uncal):
    imu, bus = uncal
    _reset_counters(bus)
    st = [1000.0, random.Random(8)]
    _run(imu, bus, st, 3.6)
    n = int(3.6 / DT)
    assert bus.transactions == {GYRO: n, ACCEL: n} and bus.writes == []
