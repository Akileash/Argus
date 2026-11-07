from __future__ import annotations

import struct
import subprocess
import sys
import time

import pytest

from argus.contracts import PROTOCOL_FOR, ModuleContext, MonotonicClock
from hardware.argus import haptics as hap_mod
from hardware.argus import imu as imu_mod
from hardware.argus import range as range_mod


def ctx(**opts):
    return ModuleContext(clock=MonotonicClock(), options=opts)


# ---------------------------------------------------------------- import hygiene
def test_no_hardware_import_at_module_import():
    code = (
        "import sys, hardware.argus.imu, hardware.argus.range, hardware.argus.hat_on, "
        "hardware.argus.env, hardware.argus.haptics, hardware.argus.camera\n"
        "bad=[m for m in ('smbus2','lgpio','picamera2','board','busio','adafruit_vl53l0x','adafruit_scd4x') if m in sys.modules]\n"
        "assert not bad, bad"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


# ---------------------------------------------------------------- IMU
class FakeI2C:
    def __init__(self):
        self.writes: list[tuple[int, int]] = []
        self.fifo = bytearray()
        self.count_override: int | None = None
        self.who = 0x68
        self.fail = False

    def write_byte(self, addr, reg, value):
        self.writes.append((reg, value))
        if reg == imu_mod.USER_CTRL and value & 0x04:
            self.fifo.clear()

    def read_byte(self, addr, reg):
        return self.who if reg == imu_mod.WHO_AM_I else 0

    def read_block(self, addr, reg, n):
        if self.fail:
            raise OSError("bus error")
        if reg == imu_mod.FIFO_COUNT_H:
            c = len(self.fifo) if self.count_override is None else self.count_override
            return bytes([c >> 8, c & 0xFF])
        assert reg == imu_mod.FIFO_R_W
        out = bytes(self.fifo[:n])
        del self.fifo[:n]
        return out

    def close(self):
        pass


def pack(*vals):
    return struct.pack(">6h", *vals)


def make_imu(bus=None, **opts):
    bus = bus or FakeI2C()
    return imu_mod.Mpu6050Imu(ctx(**opts), bus=bus, sleep=lambda s: None), bus


def test_imu_protocol_and_configure_order():
    drv, bus = make_imu()
    assert isinstance(drv, PROTOCOL_FOR["imu"])
    drv.configure()
    regs = bus.writes
    assert regs[0] == (imu_mod.PWR_MGMT_1, 0x80)  # reset first
    assert regs[1] == (imu_mod.PWR_MGMT_1, 0x01)  # wake, PLL
    d = dict(regs)
    assert d[imu_mod.ACCEL_CONFIG] == 0x18  # +-16 g
    assert d[imu_mod.GYRO_CONFIG] == 0x10  # +-1000 dps
    assert d[imu_mod.CONFIG] == 0  # DLPF 260 Hz
    assert d[imu_mod.SMPLRT_DIV] == 7  # 8 kHz / 8 = 1 kHz
    assert d[imu_mod.FIFO_EN] == 0x78  # accel + 3 gyro
    order = [r for r, _ in regs]
    # sample-rate/config set before FIFO is enabled; FIFO_EN reg last
    assert order.index(imu_mod.SMPLRT_DIV) < order.index(imu_mod.FIFO_EN)
    assert regs[-1] == (imu_mod.FIFO_EN, 0x78)
    assert (imu_mod.USER_CTRL, 0x40) in regs and (imu_mod.USER_CTRL, 0x04) in regs
    assert drv.actual_rate_hz == 1000


def test_imu_smplrt_with_dlpf_on():
    drv, bus = make_imu(dlpf_cfg=3, sample_rate_hz=500)
    drv.configure()
    assert dict(bus.writes)[imu_mod.SMPLRT_DIV] == 1  # 1 kHz / 2


def test_imu_fifo_parse_signed_big_endian_and_scale():
    drv, bus = make_imu()
    bus.fifo += pack(2048, -2048, 1024, 328, -328, 32800 - 65536)  # last is negative int16
    assert drv.drain_once() == 1
    s = drv.read()[0]
    assert (s.ax, s.ay, s.az) == (1.0, -1.0, 0.5)
    assert s.gx == pytest.approx(10.0) and s.gy == pytest.approx(-10.0)
    assert s.gz == pytest.approx((32800 - 65536) / 32.8)
    assert drv.read() == []  # drained


def test_imu_timestamps_monotonic_spaced_by_period():
    drv, bus = make_imu()
    for _ in range(5):
        bus.fifo += pack(0, 0, 2048, 0, 0, 0)
    drv.drain_once()
    ts = [s.t_ns for s in drv.read()]
    assert ts == sorted(ts)
    assert all(abs((b - a) - 1_000_000) <= 1 for a, b in zip(ts, ts[1:]))
    assert abs(ts[-1] - time.monotonic_ns()) < 50_000_000


def test_imu_partial_sample_stays_aligned():
    drv, bus = make_imu()
    bus.fifo += pack(2048, 0, 0, 0, 0, 0) + b"\x00\x01\x02"  # 3 stray bytes of next sample
    assert drv.drain_once() == 1
    assert len(bus.fifo) == 3


def test_imu_overflow_resets_fifo_and_counts_error():
    drv, bus = make_imu()
    bus.fifo += b"\x00" * 1024
    assert drv.drain_once() == 0
    h = drv.health()
    assert h.error_count == 1 and "overflow" in h.note
    assert len(bus.fifo) == 0
    assert (imu_mod.USER_CTRL, 0x44) in bus.writes


def test_imu_chunked_reads_and_axis_map():
    drv, bus = make_imu(read_chunk_samples=2, axis_map={"x": "+y", "y": "-x", "z": "+z"})
    for _ in range(5):
        bus.fifo += pack(2048, 4096, 6144, 0, 0, 0)
    assert drv.drain_once() == 5
    s = drv.read()[0]
    assert (s.ax, s.ay, s.az) == (2.0, -1.0, 3.0)


def test_imu_bad_axis_map():
    with pytest.raises(ValueError):
        imu_mod.parse_axis_map({"x": "+x", "y": "+x", "z": "+z"})


def test_imu_thread_end_to_end_and_read_nonblocking():
    drv, bus = make_imu(drain_interval_ms=5)
    drv.start()
    try:
        for _ in range(20):
            bus.fifo += pack(0, 0, 2048, 0, 0, 0)
        time.sleep(0.1)
        t0 = time.perf_counter()
        out = drv.read()
        assert time.perf_counter() - t0 < 0.005
        assert len(out) == 20
        assert drv.health().ok
    finally:
        drv.stop()
    assert not drv.health().ok


def test_imu_i2c_error_does_not_raise_and_counts():
    drv, bus = make_imu(drain_interval_ms=5)
    drv.start()
    bus.fail = True
    time.sleep(0.05)
    assert drv.read() == []
    assert drv.health().error_count >= 1
    drv.stop()


def test_imu_drain_interval_clamped_to_40ms():
    drv, _ = make_imu(drain_interval_ms=500)
    assert drv._drain_s <= 0.040


# ---------------------------------------------------------------- range
class FakeGpio:
    """pulse() immediately fires echo edges for the sensor whose trigger pin was pulsed."""

    def __init__(self, widths_us: dict[int, float | None], pins):
        self.widths = widths_us  # echo pin -> width us (None = no echo)
        self.echo_for_trig = {t: e for t, e in pins.values()}
        self.cbs = {}
        self.pulses: list[tuple[int, float, int]] = []  # (trig, perf_time, width_us)
        self.claimed_out = []

    def claim_output(self, pin, initial=0):
        self.claimed_out.append(pin)

    def claim_alert(self, pin, cb):
        self.cbs[pin] = cb

    def write(self, pin, level):
        pass

    def pulse(self, pin, width_us):
        self.pulses.append((pin, time.perf_counter(), width_us))
        e = self.echo_for_trig[pin]
        w = self.widths.get(e)
        if w is not None:
            self.cbs[e](1, 1_000_000)
            self.cbs[e](0, 1_000_000 + int(w * 1000))

    def set_pwm(self, pin, f, d):
        pass

    def close(self):
        pass


def test_width_to_mm():
    assert range_mm(5830) == pytest.approx(1000.0, abs=0.5)  # 5830 us @ 343 m/s
    assert range_mod.width_to_mm(5830, temp_c=20) == pytest.approx(5830 * 343.42 / 2000, rel=1e-6)


def range_mm(w):
    return range_mod.width_to_mm(w)


def make_range(widths=None, **opts):
    pins = range_mod.DEFAULT_PINS
    g = FakeGpio(widths or {}, pins)
    drv = range_mod.Hcsr04Array(ctx(**opts), gpio=g)
    for sid in drv._order:
        t, e = drv._pins[sid]
        g.claim_alert(e, lambda lvl, ts, p=e: drv._on_edge(p, lvl, ts))
    return drv, g


def test_range_protocol_and_defaults():
    drv, _ = make_range()
    assert isinstance(drv, PROTOCOL_FOR["range"])
    assert drv._order == ["R2", "R3"]  # MVP pair, spec §3.2a
    assert drv._pins["R1"] == (5, 6) and drv._pins["TOP"] == (20, 21)


def test_range_pulse_to_mm_and_timeout_none():
    drv, g = make_range({6: 5830.0, 26: None, 24: 1166.0, 25: 11660.0, 21: None},
                        order=["R1", "R2", "R3", "R4", "TOP"])
    out = drv.sweep_once()
    by = {s.sensor_id: s.range_mm for s in out}
    assert by["R1"] == pytest.approx(1000.0, abs=1)
    assert by["R2"] is None and by["TOP"] is None
    assert by["R3"] == pytest.approx(200.0, abs=1)
    assert by["R4"] == pytest.approx(2000.0, abs=1)
    assert all(p[2] == 10 for p in g.pulses)  # 10 us trigger


def test_range_out_of_range_width_is_none():
    drv, _ = make_range({6: 30000.0}, order=["R1"], slot_ms=5)  # ~5 m > 4 m
    assert drv.sweep_once()[0].range_mm is None


def test_range_sweep_order_and_slot_timing():
    drv, g = make_range({6: 1000.0, 26: 1000.0, 24: 1000.0, 25: 1000.0, 21: 1000.0}, order=["R1", "R3", "TOP", "R2", "R4"])
    t0 = time.perf_counter()
    out = drv.sweep_once()
    dur = time.perf_counter() - t0
    assert [s.sensor_id for s in out] == ["R1", "R3", "TOP", "R2", "R4"]
    assert [p[0] for p in g.pulses] == [5, 23, 20, 19, 16]
    gaps = [b[1] - a[1] for a, b in zip(g.pulses, g.pulses[1:])]
    assert all(0.028 <= x <= 0.06 for x in gaps), gaps
    assert 0.14 <= dur <= 0.25


def test_range_ignores_edges_from_inactive_pins():
    drv, g = make_range({}, order=["R1", "R2"], slot_ms=5)
    drv._active_echo = 6
    g.cbs[26](1, 1)  # noise on another sensor's echo
    g.cbs[26](0, 10**9)
    assert drv._width_us is None
    assert drv.sweep_once()[0].range_mm is None


def test_range_temperature_compensation():
    drv, _ = make_range({6: 5830.0}, order=["R1"], slot_ms=5, temp_c=40)
    assert drv.sweep_once()[0].range_mm == pytest.approx(5830 * (331.3 + 0.606 * 40) / 2000, abs=0.1)


def test_range_thread_read_nonblocking_and_stop():
    drv, g = make_range({6: 1000.0}, order=["R1", "R2"], slot_ms=5)
    drv.start()  # fake gpio already injected; start claims again, harmless
    time.sleep(0.1)
    t0 = time.perf_counter()
    out = drv.read()
    assert time.perf_counter() - t0 < 0.005
    assert out and out[0].sensor_id == "R1"
    assert drv.health().ok
    drv.stop()
    assert not drv.health().ok


# ---------------------------------------------------------------- haptics
class PwmGpio(FakeGpio):
    def __init__(self):
        super().__init__({}, {})
        self.pwm = []
        self.fail = False

    def set_pwm(self, pin, f, d):
        if self.fail:
            raise OSError("boom")
        self.pwm.append((pin, f, d))


def test_haptics_duty_mapping_and_pins():
    g = PwmGpio()
    h = hap_mod.L293dHaptics(ctx(), gpio=g)
    assert isinstance(h, PROTOCOL_FOR["haptics"])
    h.start()
    assert sorted(g.claimed_out) == [12, 13]
    h.set("top", 0.5)
    h.set("back", 1.0)
    h.set("top", 2.0)  # clamped
    h.set("back", -1)  # clamped
    h.set("top", float("nan"))
    assert g.pwm == [(12, 1000.0, 50.0), (13, 1000.0, 100.0), (12, 1000.0, 100.0), (13, 1000.0, 0.0), (12, 1000.0, 0.0)]
    assert h.health().ok


def test_haptics_min_max_duty_and_freq():
    g = PwmGpio()
    h = hap_mod.L293dHaptics(ctx(min_duty=0.3, max_duty=0.8, pwm_hz=500), gpio=g)
    h.start()
    h.set("top", 0.0)
    h.set("top", 1.0)
    h.set("top", 0.5)
    assert [d for _, _, d in g.pwm] == [0.0, pytest.approx(80.0), pytest.approx(55.0)]
    assert g.pwm[0][1] == 500


def test_haptics_off_all_and_failure_health():
    g = PwmGpio()
    h = hap_mod.L293dHaptics(ctx(), gpio=g)
    h.start()
    h.set("top", 1)
    h.off_all()
    assert h.duty_pct == {"top": 0.0, "back": 0.0}
    g.fail = True
    h.set("top", 1)  # must not raise
    hl = h.health()
    assert not hl.ok and hl.error_count == 1
    g.fail = False
    h.set("top", 0)
    assert h.health().ok


def test_haptics_not_started_health_false():
    h = hap_mod.L293dHaptics(ctx(), gpio=PwmGpio())
    assert not h.health().ok
