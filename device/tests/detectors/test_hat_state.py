from argus.detectors.base import DetectorState
from argus.detectors.fall_impact import FallImpactDetector
from argus.detectors.hat_state import HatStateDetector
from tests.detectors import synth_imu_tof as sy

ST = DetectorState()


def feed(hat, fall, imu_s, tof_s):
    out = []
    for kind, s in sy.merge(imu_s, tof_s):
        if kind == "imu":
            fall.on_imu(s, ST)
            hat.on_imu(s, ST)
        else:
            out += hat.on_tof(s, ST)
    return out


def calibrated():
    f = FallImpactDetector({})
    h = HatStateDetector({}, f)
    feed(h, f, [], sy.tof_series(0, 3.5, 80))
    assert h.calibrated
    return h, f


def test_calibrates_after_stable_3s():
    f = FallImpactDetector({})
    h = HatStateDetector({}, f)
    feed(h, f, [], sy.tof_series(0, 2.0, 80))
    assert not h.calibrated
    feed(h, f, [], sy.tof_series(2.0, 2.0, 80))
    assert h.calibrated and abs(h.baseline_mm - 80) < 2


def test_unstable_target_does_not_calibrate():
    f = FallImpactDetector({})
    h = HatStateDetector({}, f)
    feed(h, f, [], sy.tof_series(0, 2.0, 80) + sy.tof_series(2.0, 2.0, 200) + sy.tof_series(4.0, 2.0, 80))
    assert not h.calibrated


def test_hat_removed_calmly():
    h, f = calibrated()
    out = feed(h, f, sy.rest(3.5, 10.0, hz=500), sy.tof_series(3.5, 2.0, 80) + sy.tof_series(5.5, 6.0, 300))
    assert [d.type for d in out] == ["hat_removed"]
    assert 2.9 < (out[0].t_ns - 5.5e9) / 1e9 < 3.2
    assert not h.hat_on


def test_removed_by_no_target():
    h, f = calibrated()
    out = feed(h, f, sy.rest(3.5, 8.0, hz=500), sy.tof_series(3.5, 1.0, 80) + sy.tof_series(4.5, 5.0, None))
    assert [d.type for d in out] == ["hat_removed"]


def test_knocked_off_after_impact():
    h, f = calibrated()
    imu_s = sy.rest(3.5, 1.0) + sy.spike(4.5, 9.0, ms=5, tail_s=6.0)
    tof = sy.tof_series(3.5, 1.02, 80) + sy.tof_series(4.52, 6.0, None)
    out = feed(h, f, imu_s, tof)
    assert [d.type for d in out] == ["hat_knocked_off"]


def test_knocked_off_after_free_fall():
    h, f = calibrated()
    imu_s = sy.rest(3.5, 1.0) + sy.freefall(4.5, 0.3) + sy.rest(4.8, 6.0)
    out = feed(h, f, imu_s, sy.tof_series(3.5, 1.4, 80) + sy.tof_series(4.9, 6.0, 400))
    assert [d.type for d in out] == ["hat_knocked_off"]


def test_own_imu_tracking_without_source():
    h = HatStateDetector({"baseline_mm": 80})
    imu_s = sy.rest(3.5, 1.0) + sy.spike(4.5, 9.0, ms=5, tail_s=6.0)
    out = []
    for kind, s in sy.merge(imu_s, sy.tof_series(3.5, 1.02, 80) + sy.tof_series(4.52, 6.0, None)):
        if kind == "imu":
            h.on_imu(s, ST)
        else:
            out += h.on_tof(s, ST)
    assert [d.type for d in out] == ["hat_knocked_off"]


def test_old_impact_does_not_count():
    h, f = calibrated()
    imu_s = sy.rest(3.5, 0.5) + sy.spike(4.0, 9.0, ms=5, tail_s=10.0)
    out = feed(h, f, imu_s, sy.tof_series(3.5, 3.0, 80) + sy.tof_series(6.5, 5.0, 300))
    assert [d.type for d in out] == ["hat_removed"]


def test_hat_back_on():
    h, f = calibrated()
    tof = (sy.tof_series(3.5, 1.0, 80) + sy.tof_series(4.5, 4.0, 300)
           + sy.tof_series(8.5, 1.9, 85) + sy.tof_series(10.4, 2.0, 82))
    out = feed(h, f, sy.rest(3.5, 9.0, hz=500), tof)
    assert [d.type for d in out] == ["hat_removed", "hat_on"]
    assert h.hat_on
    assert 1.9 <= (out[1].t_ns - 8.5e9) / 1e9 <= 2.3


def test_flicker_under_3s_nothing():
    h, f = calibrated()
    tof = (sy.tof_series(3.5, 1.0, 80) + sy.tof_series(4.5, 2.5, None)
           + sy.tof_series(7.0, 1.0, 80) + sy.tof_series(8.0, 2.0, 200) + sy.tof_series(10.0, 2.0, 80))
    assert feed(h, f, [], tof) == [] and h.hat_on


def test_error_samples_ignored():
    h, f = calibrated()
    tof = (sy.tof_series(3.5, 1.0, 80) + sy.tof_series(4.5, 1.0, 300)
           + sy.tof_series(5.5, 1.0, None, status="error") + sy.tof_series(6.5, 1.0, 80))
    assert feed(h, f, [], tof) == []


def test_near_limit_clamped():
    h, f = calibrated()  # baseline 80; <50 mm reads clamp to 50 (-30 mm): still "on"
    assert feed(h, f, [], sy.tof_series(3.5, 5.0, 30, jitter=0.5)) == [] and h.hat_on


def test_near_limit_ignore_policy():
    h = HatStateDetector({"baseline_mm": 80, "below_min_policy": "ignore"})
    assert h.calibrated
    assert h.on_tof(sy.tof_series(0, 0.1, 20)[0], ST) == []


def test_preset_baseline():
    h = HatStateDetector({"baseline_mm": 90})
    out = []
    for s in sy.tof_series(0, 4.0, 200):
        out += h.on_tof(s, ST)
    assert [d.type for d in out] == ["hat_removed"]


# ---- baseline must be taken on the head, not the table (spec §4.3 boots on a table)
def test_boot_on_table_then_head_then_off_and_on_again():
    """The review's case: table at 150 mm during boot, head at 60 mm, hat off onto the table, re-don."""
    f = FallImpactDetector({})
    h = HatStateDetector({}, f)
    out = feed(h, f, [], sy.tof_series(0, 6.0, 150))  # 6 s on the table: stable, but outside the head band
    assert not h.calibrated and h.hat_on and out == []
    out = feed(h, f, [], sy.tof_series(6.0, 4.0, 60))  # put on the head
    assert h.calibrated and abs(h.baseline_mm - 60) < 2 and h.hat_on and out == []
    out = feed(h, f, [], sy.tof_series(10.0, 6.0, 150))  # take it off, onto the table
    assert [d.type for d in out] == ["hat_removed"] and not h.hat_on
    out = feed(h, f, [], sy.tof_series(16.0, 4.0, 60))  # re-don
    assert [d.type for d in out] == ["hat_on"] and h.hat_on


def test_stable_target_beyond_head_band_never_calibrates():
    f = FallImpactDetector({})
    h = HatStateDetector({}, f)
    feed(h, f, [], sy.tof_series(0, 30.0, 121))
    assert not h.calibrated


def test_baseline_max_mm_is_the_documented_fix_for_a_far_mount():
    f = FallImpactDetector({})
    h = HatStateDetector({"baseline_max_mm": 200.0}, f)
    feed(h, f, [], sy.tof_series(0, 4.0, 150))
    assert h.calibrated and abs(h.baseline_mm - 150) < 2
