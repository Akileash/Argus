"""Recorder, labels sidecar, `argus-cli record` and `trigger`."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from argus.bus import SampleBus
from argus.cli import app
from argus.contracts import ImuSample, RangeSample
from argus.recorder import Recorder, parse_expect, write_labels
from argus.tuning.score import labels_path_for
from hardware.replay.recording import read_recording


def test_parse_expect():
    assert parse_expect("struck_by_near_miss:12.5:R1") == {"type": "struck_by_near_miss", "t_s": 12.5, "sensor": "R1"}
    assert parse_expect("hat_removed") == {"type": "hat_removed"}
    with pytest.raises(ValueError):
        parse_expect("")


def test_recorder_roundtrip_and_labels(tmp_path):
    bus = SampleBus(10)
    rec = Recorder(bus, tmp_path, "walk back/wall")
    bus.publish("imu", [ImuSample(1_000, 0, 0, 1, 0, 0, 0)])
    bus.publish("range", [RangeSample(2_000, "R1", None)])
    path = rec.close()
    bus.publish("imu", [ImuSample(3_000, 0, 0, 1, 0, 0, 0)])  # after close: ignored
    write_labels(rec.labels_path, "walk", [{"type": "fall", "t_s": 1.0}], "n")
    assert path.name.endswith("_walk-back-wall.jsonl")
    assert [type(s).__name__ for s in read_recording(path)] == ["ImuSample", "RangeSample"]
    assert json.loads(rec.labels_path.read_text()) == {"label": "walk", "expected": [{"type": "fall", "t_s": 1.0}],
                                                       "notes": "n"}
    assert labels_path_for(path) == rec.labels_path  # the tuning toolkit finds it


def test_cli_record_with_mock(tmp_path):
    r = CliRunner().invoke(app, ["record", "--label", "t", "--seconds", "1", "--out-dir", str(tmp_path),
                                 "--expect", "fall:0.5", "--notes", "hi"])
    assert r.exit_code == 0, r.output
    [jl] = tmp_path.glob("*.jsonl")
    assert len(list(read_recording(jl))) > 100
    assert json.loads(jl.with_suffix(".labels.json").read_text())["expected"] == [{"type": "fall", "t_s": 0.5}]


def test_cli_trigger_rejects_unknown_type():
    r = CliRunner().invoke(app, ["trigger", "bogus"])
    assert r.exit_code == 1
