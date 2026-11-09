"""Config-loss detection/recovery (mock smbus) and fall-rule tests (no hardware)."""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

import pytest

from hat import imu as imu_mod
from hat.imu import (
    ACCEL_CONFIG, ACCEL_CONFIG2, CONFIG, GYRO_CONFIG, PWR_MGMT_1, SMPLRT_DIV,
    WHO_AM_I, FallImpactDetector, ImuSample, MPU6050,
)
from hat.wear_state import WearState

DT = 0.005


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


# --------------------------------------------------------------------------
# Stream builders (az = -1 g means upright, like the real hat)
# --------------------------------------------------------------------------
class Stream:
    def __init__(self, det, t0=1000.0, seed=1):
        self.det = det
        self.t = t0
        self.hits = []
        self.rng = random.Random(seed)

    def push(self, v):
        n = lambda: self.rng.gauss(0, 0.015)
        s = ImuSample(self.t, v[0] + n(), v[1] + n(), v[2] + n(), 0, 0, 0)
        self.t += DT
        d = self.det.push(s)
        if d is not None:
            self.hits.append(d)

    def hold(self, v, secs):
        for _ in range(int(secs / DT)):
            self.push(v)

    def ramp(self, a, b, secs):
        n = max(1, int(secs / DT))
        for i in range(n):
            f = i / n
            self.push(tuple(x + (y - x) * f for x, y in zip(a, b)))

    def spike(self, v, secs=0.03):
        self.hold(v, secs)


UP = (0.0, 0.0, -1.0)
SIDE = (-1.0, 0.0, 0.0)     # lying on the side: ~90 deg tilt
FF = (0.0, 0.0, -0.12)


def real_fall(st, end=SIDE, ff_s=0.12, impact=(0.0, 0.0, -3.4), still_s=1.0):
    st.hold(UP, 1.2)
    st.ramp(UP, FF, 0.04)
    st.hold(FF, ff_s)
    st.spike(impact)
    st.ramp((1.0, 0.5, -2.0), end, 0.15)
    st.hold(end, still_s)


def test_real_fall_shape_emits_one_high_confidence_event():
    st = Stream(FallImpactDetector())
    real_fall(st)
    st.hold(SIDE, 12.0)  # lying there afterwards must not re-fire
    assert len(st.hits) == 1
    h = st.hits[0]
    assert h.type == "fall" and h.confidence == "high" and h.peak_accel_g >= 3.0
    assert h.freefall_ms >= 100


def test_soft_landing_onto_mat_is_caught_low_confidence():
    st = Stream(FallImpactDetector())
    st.hold(UP, 1.2)
    st.ramp(UP, (0.0, 0.0, -0.3), 0.04)
    st.hold((0.0, 0.0, -0.3), 0.10)
    st.ramp((0.0, 0.0, -0.3), SIDE, 0.1)
    st.hold(SIDE, 1.0)
    assert len(st.hits) == 1 and st.hits[0].confidence == "low"


def test_event_during_wear_gate_off_is_suppressed():
    wear = WearState(history_s=30.0)
    import time as _t
    t0 = 1000.0
    for i in range(60):
        wear.update(False, t=t0 + i * 0.05)
    st = Stream(FallImpactDetector(wear_state=wear), t0=t0 + 3.0)
    real_fall(st)
    assert len(st.hits) == 1 and st.hits[0].suppressed is True


def test_second_fall_needs_rearm_but_later_fall_counts():
    st = Stream(FallImpactDetector())
    real_fall(st)
    # bounce straight into another fall shape within the cooldown
    st.hold(UP, 0.3)
    real_fall(st, still_s=0.8)
    assert len(st.hits) == 1
    st.hold(UP, 6.0)           # quiet for > cooldown + rearm
    real_fall(st)
    assert len(st.hits) == 2


# ---- things that must NOT be falls ----------------------------------------
def test_walking_is_not_a_fall():
    st = Stream(FallImpactDetector())
    for i in range(int(20 / DT)):
        ph = 2 * math.pi * 1.9 * i * DT  # ~2 steps/s
        st.push((0.15 * math.sin(ph / 2), 0.1, -(1.0 + 0.45 * math.sin(ph))))
    assert st.hits == []


def test_nodding_and_head_turns_are_not_falls():
    st = Stream(FallImpactDetector())
    for i in range(int(10 / DT)):
        a = math.radians(35) * math.sin(2 * math.pi * 0.8 * i * DT)  # nod
        st.push((math.sin(a), 0.0, -math.cos(a)))
    for i in range(int(6 / DT)):
        b = math.radians(80) * math.sin(2 * math.pi * 0.5 * i * DT)  # turn
        st.push((0.2 * math.sin(b), 0.1, -1.0))
    assert st.hits == []


def test_buzz_motor_vibration_is_ignored():
    det = FallImpactDetector()
    st = Stream(det)
    st.hold(UP, 1.0)
    for i in range(int(0.4 / DT)):
        det._last_buzz_holder["t"] = st.t  # haptics pulse in progress
        st.push((0.0, 0.0, -1.0 + 2.5 * math.sin(i * 1.7)))
    st.hold(UP, 1.0)
    assert st.hits == []


def test_jump_and_land_upright_is_not_a_fall():
    st = Stream(FallImpactDetector())
    st.hold(UP, 1.2)
    st.hold(FF, 0.20)                 # airborne
    st.spike((0.0, 0.0, -3.2))        # landing
    st.hold(UP, 3.0)                  # stands still, same orientation
    assert st.hits == []


def test_bump_then_keep_moving_is_not_a_fall():
    st = Stream(FallImpactDetector())
    st.hold(UP, 1.0)
    st.spike((2.0, 0.0, -4.0))        # head hits a beam
    for i in range(int(5 / DT)):      # keeps walking
        st.push((0.1, 0.0, -(1.0 + 0.5 * math.sin(i * 0.2))))
    assert st.hits == []


def test_hard_hit_then_lying_still_tilted_is_a_fall_candidate():
    st = Stream(FallImpactDetector())
    st.hold(UP, 1.2)
    st.spike((2.0, 0.0, -3.5))        # no freefall at all
    st.ramp((1.0, 0.0, -1.0), SIDE, 0.1)
    st.hold(SIDE, 1.0)
    assert len(st.hits) == 1 and st.hits[0].confidence == "low"


def test_hat_set_down_on_table_while_not_worn_is_gated():
    wear = WearState(history_s=30.0)
    t0 = 1000.0
    for i in range(60):
        wear.update(False, t=t0 + i * 0.05)
    st = Stream(FallImpactDetector(wear_state=wear), t0=t0 + 3.0)
    real_fall(st)                      # worst case: looks exactly like a fall
    assert all(h.suppressed for h in st.hits)


# ---- sensor config problems ------------------------------------------------
def test_stuck_8g_synthetic_trace_never_fires():
    det = FallImpactDetector()
    st = Stream(det)
    rng = random.Random(3)
    for i in range(int(60 / DT)):
        st.push((rng.gauss(0, 0.8), rng.gauss(0, 0.8), -7.8 + rng.gauss(0, 0.4)))
    assert st.hits == [] and det.implausible


def test_recorded_stuck_8g_trace_never_fires_but_old_rule_did():
    fx = json.loads((Path(__file__).parent / "fixtures" / "stuck_8g_trace.json").read_text())
    tr = fx["imu_trace"]
    det = FallImpactDetector()
    hits = []
    t0 = 1000.0
    for t_ms, ax, ay, az in tr:
        d = det.push(ImuSample(t0 + t_ms / 1000.0, ax, ay, az, 0, 0, 0))
        if d:
            hits.append(d)
    assert hits == []
    # sanity: this trace really is "resting at ~8 g"
    mags = sorted(math.sqrt(a * a + b * b + c * c) for _, a, b, c in tr)
    assert 7.0 < mags[len(mags) // 2] < 8.5


def test_config_lost_mid_fall_discards_event():
    det = FallImpactDetector()
    st = Stream(det)
    st.hold(UP, 1.2)
    st.ramp(UP, FF, 0.04)
    st.hold(FF, 0.12)
    st.spike((0.0, 0.0, -3.4))
    det.config_lost(st.t)             # driver: registers were wrong, repaired
    st.hold(SIDE, 2.0)
    assert st.hits == []
    assert det.config_purges == 1


def test_detection_resumes_after_recovery():
    det = FallImpactDetector()
    st = Stream(det)
    st.hold(UP, 1.0)
    det.config_lost(st.t)
    st.hold(UP, 3.0)                  # quiet → re-armed
    real_fall(st)
    assert len(st.hits) == 1


def test_scaled_8x_resting_after_glitch_is_blocked_until_config_lost_then_recovers():
    det = FallImpactDetector()
    st = Stream(det)
    st.hold(UP, 1.0)
    st.hold((0.0, 0.0, -7.8), 2.0)    # chip fell back to +-2 g
    assert det.implausible
    det.config_lost(st.t)             # driver re-inits
    st.hold(UP, 3.0)
    assert not det.implausible
    real_fall(st)
    assert len(st.hits) == 1
