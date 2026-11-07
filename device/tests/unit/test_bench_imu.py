"""`argus-cli test imu` must fail on FIFO overflows and, for the real driver, on a rate below ~800 Hz."""

from __future__ import annotations

from argus.bench import Bench
from argus.contracts import Health, ImuSample
from hardware.mock.clock import VirtualClock


class FakeImu:
    def __init__(self, clock, hz, errors_per_read=0):
        self.clock, self.hz, self.errors_per_read = clock, hz, errors_per_read
        self.errors = 0
        self._last = clock.now_ns()

    def start(self):
        pass

    def stop(self):
        pass

    def read(self):
        now = self.clock.now_ns()
        n = int((now - self._last) * self.hz / 1e9)
        out = [ImuSample(self._last + int(i * 1e9 / self.hz), 0.0, 0.0, 1.0, 0.0, 0.0, 0.0) for i in range(n)]
        if n:
            self._last += int(n * 1e9 / self.hz)
        self.errors += self.errors_per_read
        return out

    def health(self):
        return Health(ok=True, error_count=self.errors)


class FakeArgusImu(FakeImu):
    __module__ = "hardware.argus.imu"


def run(inst_cls, hz, errors=0):
    clock = VirtualClock()
    lines = []
    return Bench("imu", inst_cls(clock, hz, errors), clock, False, lines.append).run()


def test_mock_at_400hz_passes():
    checks = run(FakeImu, 400)
    assert not [c for c in checks if c.status == "FAIL"]


def test_real_driver_at_400hz_fails_the_rate_check():
    checks = run(FakeArgusImu, 400)
    failed = [c.name for c in checks if c.status == "FAIL"]
    assert any(n.startswith("rate >= 800") for n in failed)


def test_real_driver_at_1khz_passes():
    checks = run(FakeArgusImu, 1000)
    assert not [c for c in checks if c.status == "FAIL"]


def test_fifo_overflow_fails_even_with_a_good_rate():
    checks = run(FakeArgusImu, 1000, errors=1)
    failed = [c.name for c in checks if c.status == "FAIL"]
    assert failed == ["no FIFO overflows / driver errors"]
