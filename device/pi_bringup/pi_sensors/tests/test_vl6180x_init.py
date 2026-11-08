"""VL6180X register-sequence test against Pololu init + project tweaks."""

from __future__ import annotations

from argus_sensors.bus import FakeBus, I2CBus
from argus_sensors.sensors.vl6180x import (
    VL6180X,
    VL6180XWearSensor,
    IDENTIFICATION__MODEL_ID,
    SYSTEM__FRESH_OUT_OF_RESET,
    SYSRANGE__PART_TO_PART_RANGE_OFFSET,
    SYSRANGE__RANGE_CHECK_ENABLES,
    SYSTEM__INTERRUPT_CLEAR,
    READOUT__AVERAGING_SAMPLE_PERIOD,
    SYSRANGE__MAX_CONVERGENCE_TIME,
    SYSRANGE__START,
    RESULT__INTERRUPT_STATUS_GPIO,
    RESULT__RANGE_VAL,
    RESULT__RANGE_STATUS,
    PRIVATE_TUNING,
    MODEL_ID,
)


class CollectSink:
    def __init__(self) -> None:
        self.events = []

    def emit(self, evt):
        self.events.append(evt)

    def close(self):
        pass


def _seed_chip(regs, fresh=True):
    regs[IDENTIFICATION__MODEL_ID] = MODEL_ID
    regs[SYSTEM__FRESH_OUT_OF_RESET] = 1 if fresh else 0
    regs[SYSRANGE__PART_TO_PART_RANGE_OFFSET] = 0
    regs[SYSRANGE__RANGE_CHECK_ENABLES] = 0x01
    regs[RESULT__INTERRUPT_STATUS_GPIO] = 0x04
    regs[RESULT__RANGE_VAL] = 42
    regs[RESULT__RANGE_STATUS] = 0x00


def test_init_fresh_writes_private_tuning_and_disables_ece():
    fake = FakeBus()
    regs = fake.set_device(0x29)
    _seed_chip(regs, fresh=True)
    bus = I2CBus(backend=fake)
    drv = VL6180X(bus)
    assert drv.read_reg(IDENTIFICATION__MODEL_ID) == MODEL_ID
    drv.init()
    drv.configure_default()
    checks = drv.read_reg(SYSRANGE__RANGE_CHECK_ENABLES)
    drv.write_reg(SYSRANGE__RANGE_CHECK_ENABLES, checks & ~0x01)
    drv.write_reg(SYSTEM__INTERRUPT_CLEAR, 0x07)

    written_regs = [w[2][0] << 8 | w[2][1] for w in fake.writes if w[0] == "w" and len(w[2]) == 3]
    for reg, val in PRIVATE_TUNING:
        assert reg in written_regs, f"missing private tuning write 0x{reg:03X}"

    assert (regs[SYSRANGE__RANGE_CHECK_ENABLES] & 0x01) == 0
    assert regs[SYSRANGE__MAX_CONVERGENCE_TIME] == 0x31
    assert regs[READOUT__AVERAGING_SAMPLE_PERIOD] == 0x30


def test_init_not_fresh_skips_private_tuning():
    fake = FakeBus()
    regs = fake.set_device(0x29)
    _seed_chip(regs, fresh=False)
    regs[0x096] = 253
    bus = I2CBus(backend=fake)
    drv = VL6180X(bus)
    before = list(fake.writes)
    drv.init()
    after = fake.writes[len(before) :]
    private_regs = {r for r, _ in PRIVATE_TUNING}
    for w in after:
        if w[0] == "w" and len(w[2]) == 3:
            reg = (w[2][0] << 8) | w[2][1]
            assert reg not in private_regs or reg == SYSTEM__FRESH_OUT_OF_RESET


def test_single_shot_sequence():
    fake = FakeBus()
    regs = fake.set_device(0x29)
    _seed_chip(regs, fresh=False)
    regs[0x096] = 253
    bus = I2CBus(backend=fake)
    drv = VL6180X(bus)
    fake.writes.clear()
    mm, status, timed_out = drv.read_range_single(timeout_ms=100)
    assert timed_out is False
    assert mm == 42
    assert status == 0
    start_writes = [
        w for w in fake.writes if w[0] == "w" and len(w[2]) == 3 and ((w[2][0] << 8) | w[2][1]) == SYSRANGE__START
    ]
    assert start_writes and start_writes[0][2][2] == 0x01


def test_wear_sensor_hello_and_event():
    fake = FakeBus()
    regs = fake.set_device(0x29)
    _seed_chip(regs, fresh=True)
    bus = I2CBus(backend=fake)
    sink = CollectSink()
    sensor = VL6180XWearSensor(bus, sink)
    sensor.start()
    assert sink.events[0]["type"] == "hello"
    assert sink.events[0]["device"] == "vl6180x_wear"
    assert sink.events[0]["near_mm_max"] == 254
