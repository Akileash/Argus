"""Replay the teammates' sample files through the mapper and validate against contracts/*.schema.json."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from argus.bridge import mapper
from argus.events.queue import validate_event, validate_trace

from .conftest import SAMPLES, T0

SIDE = {"left": "R3", "right": "R2"}


def load_events() -> list[dict]:
    out = [json.loads(p.read_text()) for p in sorted(SAMPLES.glob("*.json"))]
    out += [json.loads(line) for line in (SAMPLES / "events.sample.jsonl").read_text().splitlines() if line.strip()]
    return out


def mappable() -> list[dict]:
    return [e for e in load_events() if mapper.is_hat_event(e)]


def test_samples_cover_both_hat_event_types():
    assert {e["type"] for e in mappable()} == {"fall", "line_of_fire"}


@pytest.mark.parametrize("raw", mappable(), ids=lambda e: f"{e['type']}-{e['event_id'][:6]}")
def test_every_sample_maps_to_a_valid_event_and_trace(raw):
    m = mapper.map_hat_event(raw, serial="ARG-0001", side_map=SIDE)
    t0 = mapper.parse_ts(raw["timestamp"])
    mapper.with_clip(m.event, t0, t0 + timedelta(seconds=5), 8.7)
    validate_event(m.event)
    validate_trace(m.trace)
    assert m.event["device_serial"] == "ARG-0001"  # from pairing, never their hat_id
    assert m.event["event_id"] == raw["event_id"]
    assert m.event["t0"].endswith("Z")


def test_non_events_are_not_mapped():
    skipped = [e for e in load_events() if not mapper.is_hat_event(e)]
    assert skipped, "the samples include wear_event / air_sample lines, which are not hat events"
    assert not mapper.is_hat_event({"type": "impact", "event_id": "x", "timestamp": "2026-01-01T00:00:00Z"})
    assert not mapper.is_hat_event({"type": "fall"})
    assert not mapper.is_hat_event("fall")


def test_fall_maps_per_spec_section_6():
    raw = json.loads((SAMPLES / "fall.sample.json").read_text())
    m = mapper.map_hat_event(raw, serial="S", side_map=SIDE)
    e = m.event
    assert (e["type"], e["device_class"]) == ("fall", "incident")
    assert e["t0"] == "2026-11-08T22:30:00.000Z"  # 15:30-07:00 -> UTC
    assert e["metrics"]["peak_g"] == 3.42 and e["metrics"]["free_fall_ms"] == 120.0
    assert e["metrics"]["drop_height_m"] == pytest.approx(0.5 * 9.81 * 0.12**2, abs=1e-3)
    assert e["metrics"]["hat_on"] is True and e["metrics"]["sensor_id"] is None


def test_line_of_fire_is_always_a_near_miss_with_mapped_sensor():
    raw = json.loads((SAMPLES / "line_of_fire.sample.json").read_text())
    m = mapper.map_hat_event(raw, serial="S", side_map=SIDE)
    assert (m.event["type"], m.event["device_class"]) == ("struck_by_near_miss", "near_miss")
    assert m.event["metrics"]["sensor_id"] == "R3"  # their left -> R3 (PM decision; needs-teammate to confirm)
    assert m.event["metrics"]["range_m"] == 0.311 and m.event["metrics"]["closing_rate_mps"] == 2.6
    assert m.event["metrics"]["ttc_s"] == pytest.approx(0.311 / 2.6, abs=1e-3)
    assert m.trace["ranges"]["R3"]["range_m"] == [0.311]
    right = mapper.map_hat_event({**raw, "side": "right"}, serial="S", side_map=SIDE)
    assert right.event["metrics"]["sensor_id"] == "R2"


def test_unmapped_side_is_noted_not_guessed():
    raw = json.loads((SAMPLES / "line_of_fire.sample.json").read_text())
    m = mapper.map_hat_event({**raw, "side": None}, serial="S", side_map=SIDE)
    assert m.event["metrics"]["sensor_id"] is None and m.notes
    validate_event(m.event)


def test_trace_keeps_real_samples_and_real_rate():
    raw = json.loads((SAMPLES / "fall.sample.json").read_text())
    rows = raw["sensor"]["imu_trace"]
    m = mapper.map_hat_event(raw, serial="S", side_map=SIDE)
    imu = m.trace["imu"]
    assert len(imu["t"]) == len(rows) == len(imu["ax"])  # no samples added or dropped
    assert imu["t"][0] == rows[0][0] / 1000.0 and imu["az"][5] == rows[5][3]
    n = len(rows)
    assert imu["gx"] == imu["gy"] == imu["gz"] == [None] * n  # their trace has no gyro: null, never zeros
    det = m.trace["detections"][0]
    assert det["imu_rate_hz"] == 50.0  # the sample's real 20 ms spacing, not the 200 Hz nominal / 400 Hz contract
    assert m.trace["t_end_s"] == 0.06


def test_bad_imu_rows_are_dropped_and_noted():
    raw = json.loads((SAMPLES / "fall.sample.json").read_text())
    raw["sensor"]["imu_trace"] = [[0, 0, 0, 1], [1, "x", 0, 1], [2, 0, 0], None, [3, 0, 0, 1]]
    m = mapper.map_hat_event(raw, serial="S", side_map=SIDE)
    assert len(m.trace["imu"]["t"]) == 2 and any("dropped" in n for n in m.notes)
    validate_trace(m.trace)


def test_fall_without_trace_is_still_valid():
    raw = json.loads((SAMPLES / "fall.sample.json").read_text())
    del raw["sensor"]["imu_trace"]
    m = mapper.map_hat_event(raw, serial="S", side_map=SIDE)
    validate_event(m.event)
    validate_trace(m.trace)
    assert m.notes


def test_air_becomes_environment_context():
    raw = json.loads((SAMPLES / "fall.sample.json").read_text())
    air = json.loads((SAMPLES / "air_sample.sample.json").read_text())
    m = mapper.map_hat_event(raw, serial="S", side_map=SIDE, air=air)
    assert m.event["env"] == {"co2_ppm": 555.0, "temp_c": 25.29, "rh_pct": 22.71}
    assert m.trace["env"] == m.event["env"]
    validate_event(m.event)
    assert mapper.map_hat_event(raw, serial="S", side_map=SIDE).event["env"] is None


def test_fall_within_2s_of_a_near_miss_links_to_it():
    raw = json.loads((SAMPLES / "fall.sample.json").read_text())
    t0 = mapper.parse_ts(raw["timestamp"])
    m = mapper.map_hat_event(raw, serial="S", side_map=SIDE, recent_near_misses=[
        ("a", t0 - timedelta(seconds=1.5)), ("b", t0 - timedelta(seconds=5)), ("c", t0 + timedelta(seconds=1))])
    assert m.event["merged_triggers"] == [{"type": "struck_by_near_miss", "offset_s": -1.5}]
    validate_event(m.event)


def test_hat_removed_is_an_event_only_ppe_log():
    m = mapper.map_hat_off(event_id="11111111-1111-4111-8111-111111111111", off_start=T0, detected=T0, serial="S",
                           fall=None, fall_t0=None, air=None)
    validate_event(m.event)
    assert (m.event["type"], m.event["device_class"]) == ("hat_removed", "ppe_log")
    assert m.trace is None and not m.wants_clip and m.event["metrics"]["hat_on"] is False


def test_hat_knocked_off_shares_the_falls_metrics_and_trace_shifted():
    raw = json.loads((SAMPLES / "fall.sample.json").read_text())
    f0 = mapper.parse_ts(raw["timestamp"])
    m = mapper.map_hat_off(event_id="22222222-2222-4222-8222-222222222222", off_start=f0 + timedelta(seconds=1),
                           detected=f0 + timedelta(seconds=4), serial="S", fall=raw, fall_t0=f0, air=None)
    mapper.with_clip(m.event, f0 + timedelta(seconds=1), f0 + timedelta(seconds=6), 8.7)
    validate_event(m.event)
    validate_trace(m.trace)
    assert (m.event["type"], m.event["device_class"]) == ("hat_knocked_off", "incident")
    assert m.event["metrics"]["peak_g"] == 3.42 and m.event["metrics"]["hat_on"] is False
    assert m.event["window"] == {"pre_s": 3.7, "post_s": 5.0}  # from the real clip length and end time
    assert m.trace["imu"]["t"][0] == pytest.approx(-0.08 - 1.0)  # same samples, relative to the new t0


def test_window_comes_from_the_real_clip_and_is_clamped():
    t0 = T0
    assert mapper.clip_window(t0, t0 + timedelta(seconds=5), 8.7) == {"pre_s": 3.7, "post_s": 5.0}
    assert mapper.clip_window(t0, t0 + timedelta(seconds=60), 8.7) == {"pre_s": 0.0, "post_s": 8.7}
    assert mapper.clip_window(t0, t0 - timedelta(seconds=3), 8.7) == {"pre_s": 8.7, "post_s": 0.0}
    ev: dict = {}
    mapper.with_clip(ev, t0, t0, None)  # length unknown: nothing assumed
    assert ev["window"] == {"pre_s": 0.0, "post_s": 0.0}


def test_hat_on_for_a_fall_is_theirs_else_the_last_tof_state():
    raw = json.loads((SAMPLES / "fall.sample.json").read_text())
    assert mapper.reported_worn(raw) is True
    del raw["sensor"]["helmet_worn_at_onset"]
    assert mapper.reported_worn(raw) is None
    assert mapper.map_hat_event(raw, serial="S", side_map=SIDE, hat_on=False).event["metrics"]["hat_on"] is False


def test_extra_detail_goes_only_into_trace_detections():
    raw = json.loads((SAMPLES / "fall.sample.json").read_text())
    m = mapper.map_hat_event(raw, serial="S", side_map=SIDE)
    det = m.trace["detections"][0]
    assert det["wear_on_fraction"] == 0.87 and det["helmet_worn_lookback_s"] == 3.0
    assert set(m.event["metrics"]) <= set(mapper.METRIC_KEYS)
    assert set(m.event["firmware"]["impls"].values()) == {"team"} and len(m.event["firmware"]["impls"]) == 6
