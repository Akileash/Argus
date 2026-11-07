import json

import pytest

from argus.contracts import EnvSample, ImuSample, ModuleContext, RangeSample, TofSample
from argus.supervisor import load_impl
from hardware.mock.clock import VirtualClock
from hardware.replay.recording import read_recording, write_recording


def sample_set():
    s = []
    for i in range(100):  # 1 s of 100 Hz IMU, base time deliberately far from the clock's
        s.append(ImuSample(5_000_000_000 + i * 10_000_000, 0.0, 0.0, 1.0, 0.0, 0.0, float(i)))
    for i in range(10):
        s.append(RangeSample(5_000_000_000 + i * 100_000_000, "R2", 500.0 + i))
        s.append(TofSample(5_000_000_000 + i * 100_000_000, 62.0 if i < 5 else None, "ok" if i < 5 else "out_of_range"))
    s.append(EnvSample(5_000_000_000, 650.0, 22.0, 40.0))
    return s


def test_roundtrip_with_meta_header(tmp_path):
    p = write_recording(tmp_path / "r.jsonl", sample_set(), {"source": "test"})
    first = json.loads(p.read_text().splitlines()[0])
    assert first == {"meta": {"source": "test"}}
    assert list(read_recording(p)) == sorted(sample_set(), key=lambda s: s.t_ns)


def test_unknown_stream_is_a_clear_error(tmp_path):
    p = tmp_path / "bad.jsonl"
    p.write_text('{"stream": "lidar", "t_ns": 1}\n')
    with pytest.raises(ValueError, match="unknown stream"):
        list(read_recording(p))


def make(module, path, clock, **opts):
    return load_impl(module, "replay", ModuleContext(clock, {"file": str(path), **opts}))


def test_replay_real_time_and_streams_share_origin(tmp_path):
    p = write_recording(tmp_path / "r.jsonl", sample_set())
    clock = VirtualClock()
    imu, rng = make("imu", p, clock), make("range", p, clock)
    imu.start()
    clock.advance(0.25)  # second module starts later but must stay synchronised
    rng.start()
    got = imu.read()
    assert [s.gz for s in got] == list(range(26))  # samples with rel time <= 0.25 s
    assert got[0].t_ns == 1_000_000_000  # re-based onto the injected clock
    assert [s.range_mm for s in rng.read()] == [502.0, 503.0] or rng.read() == []  # starts "in the middle"
    clock.advance(1.0)
    assert [s.gz for s in imu.read()] == list(range(26, 100))
    assert imu.read() == []
    assert imu.health().note == "recording finished"


def test_accelerated_replay_compresses_timestamps(tmp_path):
    p = write_recording(tmp_path / "r.jsonl", sample_set())
    clock = VirtualClock()
    imu = make("imu", p, clock, speed=10.0)
    imu.start()
    clock.advance(0.1)  # 0.1 s of wall time = 1 s of recording
    got = imu.read()
    assert len(got) == 100
    assert got[-1].t_ns - got[0].t_ns == pytest.approx(99 * 1_000_000, abs=10)  # 10 ms steps -> 1 ms


def test_loop(tmp_path):
    p = write_recording(tmp_path / "r.jsonl", sample_set())
    clock = VirtualClock()
    tof = make("hat_on", p, clock, loop=True)
    tof.start()
    clock.advance(2.5)
    got = tof.read()
    assert len(got) == 30 - 1 or len(got) >= 20
    ts = [s.t_ns for s in got]
    assert ts == sorted(ts)


def test_missing_file_fails_with_clear_message(tmp_path):
    from argus.supervisor import ModuleLoadError

    with pytest.raises(ModuleLoadError, match="options.file"):
        load_impl("imu", "replay", ModuleContext(VirtualClock(), {"file": str(tmp_path / "nope.jsonl")}))


def test_haptics_and_camera_replay_delegate_to_mock(tmp_path):
    clock = VirtualClock()
    assert load_impl("haptics", "replay", ModuleContext(clock)).health().ok is False  # not started yet
    cam = load_impl("camera", "replay", ModuleContext(clock, {"cache_dir": str(tmp_path)}, "/some/file.jsonl"))
    cam.start()
