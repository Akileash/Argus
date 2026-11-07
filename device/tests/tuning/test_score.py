"""Scoring edge cases: matching, tolerance, false alarms, lead time."""

from __future__ import annotations

import pytest

from argus.detectors.base import Detection
from argus.tuning.score import aggregate, match_recording, parse_labels

NS = 1_000_000_000


def det(t_s, type_="struck_by_near_miss", sensor="R1"):
    return Detection(t_ns=round(t_s * NS), type=type_, sensor_id=sensor)


def score(raw_labels, dets, duration_s=60.0, tol=0.75):
    r = match_recording("r", parse_labels(raw_labels), dets, 0, duration_s, tol)
    return r, aggregate([r], tol)


def test_exact_match_and_timing_error():
    _r, sc = score({"expected": [{"type": "fall", "t_s": 10.0}]}, [det(10.3, "fall", None)])
    assert sc.overall.tp == 1 and sc.overall.fp == 0 and sc.overall.misses == 0
    assert sc.per_type["fall"].median_err_s == pytest.approx(0.3)


def test_tolerance_boundary_inclusive_and_outside_is_miss_plus_fp():
    _, ok = score({"expected": [{"type": "fall", "t_s": 10.0}]}, [det(10.75, "fall", None)])
    assert ok.overall.tp == 1
    _, bad = score({"expected": [{"type": "fall", "t_s": 10.0}]}, [det(10.8, "fall", None)])
    assert (bad.overall.tp, bad.overall.misses, bad.overall.fp) == (0, 1, 1)


def test_tolerance_configurable():
    _, sc = score({"expected": [{"type": "fall", "t_s": 10.0}]}, [det(11.5, "fall", None)], tol=2.0)
    assert sc.overall.tp == 1


def test_wrong_type_is_miss_and_fp():
    _, sc = score({"expected": [{"type": "fall", "t_s": 5}]}, [det(5.0, "head_impact", None)])
    assert (sc.overall.tp, sc.overall.misses, sc.overall.fp) == (0, 1, 1)


def test_sensor_constraint_str_and_list():
    for sens in ("R2", ["R2", "R3"]):
        _, sc = score({"expected": [{"type": "struck_by_near_miss", "t_s": 5, "sensor": sens}]}, [det(5.0, sensor="R1")])
        assert sc.overall.tp == 0 and sc.overall.fp == 1
    _, sc = score({"expected": [{"type": "struck_by_near_miss", "t_s": 5, "sensors": ["R1", "R2"]}]}, [det(5.0)])
    assert sc.overall.tp == 1


def test_one_to_one_duplicate_detection_is_false_positive():
    _, sc = score({"expected": [{"type": "fall", "t_s": 5}]}, [det(5.0, "fall", None), det(5.4, "fall", None)])
    assert (sc.overall.tp, sc.overall.fp) == (1, 1)


def test_closest_detection_wins():
    r, _ = score({"expected": [{"type": "fall", "t_s": 5}]}, [det(4.5, "fall", None), det(5.1, "fall", None)])
    assert r.matches[0].t_det_s == pytest.approx(5.1)
    assert [round(t, 1) for _, t in r.false_positives] == [4.5]


def test_no_expected_means_every_event_detection_is_false_alarm_rate_per_hour():
    # 2 FPs in a 30 min recording -> 4/h
    _, sc = score({"expected": []}, [det(100, "fall", None), det(900, "hat_removed", None)], duration_s=1800)
    assert sc.overall.fp == 2 and sc.overall.expected == 0
    assert sc.overall.fa_per_hour == pytest.approx(4.0)
    assert sc.overall.detection_rate is None
    assert sc.per_type["fall"].fa_per_hour == pytest.approx(2.0)


def test_non_event_types_are_ignored():
    _, sc = score({"expected": []}, [det(1, "struck_by_warning"), det(2, "hat_on", None)])
    assert sc.overall.fp == 0 and sc.overall.detections == 0


def test_zero_duration_no_division_error():
    _, sc = score({"expected": []}, [det(0.0, "fall", None)], duration_s=0.0)
    assert sc.overall.fa_per_hour == 0.0 and sc.overall.fp == 1


def test_expected_without_time_matches_anywhere():
    _, sc = score({"expected": [{"type": "hat_removed"}]}, [det(42.0, "hat_removed", None)])
    assert sc.overall.tp == 1


def test_window_label_overrides_tolerance():
    raw = {"expected": [{"type": "hat_removed", "t_window_s": [5.0, 9.0]}]}
    _, sc = score(raw, [det(8.5, "hat_removed", None)])
    assert sc.overall.tp == 1
    _, sc = score(raw, [det(9.2, "hat_removed", None)])
    assert sc.overall.tp == 0


def test_warning_lead_time():
    dets = [det(3.4, "struck_by_warning"), det(3.6, "struck_by_warning"), det(4.0, "struck_by_near_miss")]
    r, sc = score({"expected": [{"type": "struck_by_near_miss", "t_s": 4.2}]}, dets)
    assert r.matches[0].lead_s == pytest.approx(0.8)  # first warning to labelled time
    assert sc.overall.median_lead_s == pytest.approx(0.8)


def test_warning_after_labelled_time_gives_no_lead():
    dets = [det(5.0, "struck_by_warning"), det(5.0, "struck_by_near_miss")]
    r, _ = score({"expected": [{"type": "struck_by_near_miss", "t_s": 4.5}]}, dets)
    assert r.matches[0].lead_s is None


def test_contact_upgrade_pair_is_one_event():
    dets = [det(4.0, "struck_by_near_miss"), det(4.6, "struck_by_contact")]
    _, sc = score({"expected": [{"type": "struck_by_contact", "t_s": 4.6}]}, dets)
    assert (sc.overall.tp, sc.overall.fp) == (1, 0)
    _, sc = score({"expected": [{"type": "struck_by_near_miss", "t_s": 4.0}]}, dets)
    assert (sc.overall.tp, sc.overall.fp) == (1, 0)


def test_lone_unexpected_contact_is_false_positive():
    _, sc = score({"expected": []}, [det(4.6, "struck_by_contact")])
    assert sc.overall.fp == 1


def test_aggregate_across_recordings_sums_hours():
    a = match_recording("a", parse_labels({"expected": []}), [det(1, "fall", None)], 0, 1800)
    b = match_recording("b", parse_labels({"expected": []}), [], 0, 1800)
    sc = aggregate([a, b])
    assert sc.total_hours == pytest.approx(1.0) and sc.overall.fa_per_hour == pytest.approx(1.0)
