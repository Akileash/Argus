from argus.detectors.base import DetectorState
from argus.detectors.fall_impact import FallImpactDetector
from tests.detectors import synth_imu_tof as sy

ST = DetectorState()


def run(det, samples):
    out = []
    for s in samples:
        out += det.on_imu(s, ST)
    return out


def test_one_metre_drop_is_single_fall():
    d = FallImpactDetector({})
    stream = sy.rest(0, 1.0) + sy.drop(1.0, 0.452, landing_g=12) + sy.rest(1.8, 1.0)
    out = run(d, stream)
    assert [x.type for x in out] == ["fall"]
    f = out[0]
    assert abs(f.drop_height_m - 1.0) < 0.15
    assert 400 < f.free_fall_ms < 500
    assert 11 < f.peak_g <= 12.5 and not f.peak_g_saturated


def test_fall_with_bounce_is_one_event():
    d = FallImpactDetector({})
    # landing at 0.95 s, bounce 120 ms later (inside the debounce window)
    stream = (sy.rest(0, 0.5) + sy.freefall(0.5, 0.45) + sy.spike(0.95, 12.0, ms=6, tail_s=0.114)
              + sy.spike(1.07, 8.0, ms=5) + sy.rest(1.3, 0.5))
    assert [x.type for x in run(d, stream)] == ["fall"]


def test_saturated_hit_head_impact():
    d = FallImpactDetector({})
    out = run(d, sy.rest(0, 0.5) + sy.spike(0.5, 20.0, ms=5) + sy.rest(0.8, 0.5))
    assert [x.type for x in out] == ["head_impact"]
    assert out[0].peak_g_saturated and out[0].peak_g >= 15.9


def test_unsaturated_impact_peak():
    d = FallImpactDetector({})
    out = run(d, sy.rest(0, 0.2) + sy.spike(0.2, 9.0, ms=6) + sy.rest(0.5, 0.3))
    assert len(out) == 1 and not out[0].peak_g_saturated and 8 < out[0].peak_g <= 9.1


def test_walking_and_nodding_nothing():
    assert run(FallImpactDetector({}), sy.walking(0, 10.0)) == []
    for hz in (100, 400):
        assert run(FallImpactDetector({}), sy.walking(0, 5.0, hz=hz)) == []


def test_below_threshold_impact_ignored():
    d = FallImpactDetector({})
    assert run(d, sy.rest(0, 0.2) + sy.spike(0.2, 5.0, ms=6) + sy.rest(0.5, 0.3)) == []


def test_short_free_fall_not_a_fall():
    d = FallImpactDetector({})
    assert run(d, sy.rest(0, 0.5) + sy.drop(0.5, 0.15, landing_g=4) + sy.rest(1.0, 0.5)) == []


def test_free_fall_without_landing_nothing():
    d = FallImpactDetector({})
    assert run(d, sy.rest(0, 0.5) + sy.freefall(0.5, 0.4) + sy.rest(0.9, 2.0)) == []


def test_one_impact_one_detection_with_double_pulse():
    d = FallImpactDetector({})
    stream = sy.rest(0, 0.2) + sy.spike(0.2, 10.0, ms=4, tail_s=0.008) + sy.spike(0.212, 7.0, ms=4) + sy.rest(0.5, 0.5)
    assert [x.type for x in run(d, stream)] == ["head_impact"]


def test_1khz_spike_caught_100hz_misses():
    """Documented: a 3 ms pulse needs full-rate sampling; a 100 Hz downsample can skip it."""
    # pulse occupies 0.5031-0.5061 s, between the 10 ms ticks at 0.503 and 0.513
    full = sy.rest(0, 0.5031) + sy.spike(0.5031, 10.0, ms=3) + sy.rest(0.7, 0.5)
    assert [x.type for x in run(FallImpactDetector({}), full)] == ["head_impact"]
    down = [s for s in full if (s.t_ns // 1_000_000) % 10 == 0]
    assert run(FallImpactDetector({}), down) == []


def test_recent_impact():
    d = FallImpactDetector({})
    run(d, sy.rest(0, 1.0) + sy.spike(1.0, 8.0, ms=5) + sy.rest(1.2, 3.0))
    ns = 1_000_000_000
    assert d.recent_impact(int(2.0 * ns), int(2 * ns))
    assert not d.recent_impact(int(4.0 * ns), int(2 * ns))


def test_recent_impact_free_fall():
    d = FallImpactDetector({})
    run(d, sy.rest(0, 0.5) + sy.freefall(0.5, 0.4) + sy.rest(0.9, 1.0))
    assert d.recent_impact(int(1.0e9), int(1e9))
