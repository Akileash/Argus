"""Event schema must match the real Arduino sample_output lines."""

from __future__ import annotations

import json
from pathlib import Path

from argus_sensors import events

LEGACY = Path(__file__).resolve().parents[2] / "handoff" / "Argus_Sensors_Handoff"


def _load_samples(rel: str):
    path = LEGACY / rel
    if not path.exists():
        alt = Path(__file__).resolve().parents[1] / "docs" / "legacy_arduino" / rel
        path = alt
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines]


def test_wear_event_keys_match_sample():
    samples = _load_samples("TimeOfFlight/sample_output/wear_events.sample.jsonl")
    wear = next(s for s in samples if s["type"] == "wear_event")
    evt = events.wear_event(
        helmet_worn=True,
        state="ON",
        distance_mm=28,
        status=0,
        near=True,
        sensor_ok=True,
        recoveries=0,
    )
    for key in wear:
        if key == "received_at":
            continue
        assert key in evt, f"missing key {key} in wear_event"
    assert evt["type"] == "wear_event"
    assert isinstance(evt["helmet_worn"], bool)
    assert evt["state"] in ("ON", "OFF")


def test_hello_tof_keys():
    samples = _load_samples("TimeOfFlight/sample_output/wear_events.sample.jsonl")
    hello = next(s for s in samples if s["type"] == "hello")
    evt = events.hello_tof(
        helmet_worn=False,
        state="OFF",
        distance_mm=None,
        status=7,
        near=False,
        sensor_ok=True,
        recoveries=0,
    )
    for key in ("type", "device", "near_mm_min", "near_mm_max", "on_dwell_ms", "off_dwell_ms", "helmet_worn"):
        assert key in evt
    assert evt["device"] == hello["device"]
    assert evt["near_mm_max"] == 254


def test_air_sample_keys_match_sample():
    samples = _load_samples("SCD41/sample_output/air_events.sample.jsonl")
    air = next(s for s in samples if s["type"] == "air_sample")
    evt = events.air_sample(
        co2_ppm=261,
        temp_c=22.93,
        rh_pct=21.61,
        sensor_ok=True,
        reads=1,
        recoveries=0,
    )
    for key in air:
        if key == "received_at":
            continue
        assert key in evt, f"missing key {key} in air_sample"


def test_sensor_recover_keys():
    evt = events.sensor_recover(ok=True, recoveries=1)
    assert evt["type"] == "sensor_recover"
    assert evt["ok"] is True
    assert evt["recoveries"] == 1
    assert "t_ms" in evt
    assert "received_at" in evt
