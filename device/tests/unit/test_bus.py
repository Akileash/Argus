import threading

import pytest

from argus.bus import SampleBus
from argus.contracts import ImuSample, RangeSample


def imu(t_ms: int) -> ImuSample:
    return ImuSample(t_ms * 1_000_000, 0, 0, 1, 0, 0, 0)


def test_window_is_inclusive_and_sorted():
    bus = SampleBus(35)
    bus.publish("imu", [imu(t) for t in range(0, 1000, 10)])
    w = bus.window("imu", 100_000_000, 200_000_000)
    assert [s.t_ns // 1_000_000 for s in w] == list(range(100, 201, 10))
    assert bus.window("imu", 5_000_000_000, 6_000_000_000) == []


def test_ring_evicts_old_samples_by_time():
    bus = SampleBus(history_s=2.0)
    bus.publish("imu", [imu(t) for t in range(0, 10_000, 10)])  # 10 s of 100 Hz
    oldest = bus.window("imu", 0, 1 << 62)[0].t_ns
    assert oldest >= (10_000 - 2_000 - 200 - 10) * 1_000_000  # history + 10% slack
    assert bus.latest("imu").t_ns == 9_990 * 1_000_000
    assert len(bus) < 300


def test_default_history_is_35_s():
    assert SampleBus().history_s == 35.0


def test_out_of_order_sample_keeps_ring_sorted():
    bus = SampleBus()
    bus.publish("imu", [imu(10), imu(30), imu(20)])
    assert [s.t_ns // 1_000_000 for s in bus.window("imu", 0, 99_000_000)] == [10, 20, 30]


def test_fan_out_and_unsubscribe():
    bus = SampleBus()
    a, b = [], []
    unsub = bus.subscribe("imu", a.append)
    bus.subscribe("imu", b.append)
    bus.publish("imu", [imu(1)])
    unsub()
    bus.publish("imu", [imu(2)])
    assert len(a) == 1 and len(b) == 2


def test_bad_subscriber_does_not_stop_others_or_storage():
    bus = SampleBus()
    got = []

    def boom(_):
        raise RuntimeError("x")

    bus.subscribe("range", boom)
    bus.subscribe("range", got.append)
    assert bus.publish("range", [RangeSample(1, "R1", 100.0)]) == 1
    assert len(got) == 1 and bus.latest("range").sensor_id == "R1"


def test_unknown_stream():
    with pytest.raises(KeyError, match="unknown stream"):
        SampleBus().window("nope", 0, 1)


def test_threaded_publishers_lose_nothing():
    bus = SampleBus(history_s=1000)

    def pub(base: int) -> None:
        for i in range(500):
            bus.publish("imu", [imu(base + i)])

    ts = [threading.Thread(target=pub, args=(k * 1000,)) for k in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    got = bus.window("imu", 0, 1 << 62)
    assert len(got) == 2000
    assert [s.t_ns for s in got] == sorted(s.t_ns for s in got)
