"""End to end with a team module live: range comes from a fake teammate script playing the board-swing approach
(scenario ``board_swing_rear``), the real detectors fire, and a schema-valid event + trace land in the queue.
Real clock and threads, like ``argus-cli run`` (about 10 s)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import jsonschema
import pytest

from argus.bus import SampleBus
from argus.config import load_config
from argus.contracts import ModuleContext, MonotonicClock
from argus.pipeline import Pipeline
from argus.supervisor import Supervisor
from hardware.mock.clock import VirtualClock
from tests.e2e.harness import StubCamera

from .conftest import team, write_config
from .test_live_swap import wait_for

CONTRACTS = Path(__file__).parents[3] / "contracts"


def write_truth(path: Path, scenario: str) -> None:
    """The scenario's range stream (mm, null = no echo) as the 'ground truth' the fake teammate plays back."""
    from hardware.mock.range import MockRange

    clock = VirtualClock()
    m = MockRange(ModuleContext(clock, {}, scenario))
    m.start()
    t0, rows = clock.now_ns(), []
    for _ in range(int(6.5 / 0.03)):
        clock.advance(0.03)
        rows += [{"t_s": (s.t_ns - t0) / 1e9, "sensor_id": s.sensor_id, "range_mm": s.range_mm} for s in m.read()]
    path.write_text("\n".join(json.dumps(r) for r in rows))


@pytest.mark.timeout(60)
def test_board_swing_from_a_team_range_script_produces_a_valid_event(tmp_path):
    truth = tmp_path / "truth.jsonl"
    write_truth(truth, "board_swing_rear")
    cfg_path = write_config(
        tmp_path,
        {"range": team("range", command=["python", str(Path(__file__).parent / "fakes" / "fake_range.py"),
                                         "--style", "json", "--truth", str(truth)])},
        events={"post_roll_s": 1.0, "queue_dir": str(tmp_path / "q")})
    cfg = load_config(cfg_path)
    bus = SampleBus(cfg.bus.history_s)
    sup = Supervisor(cfg, bus, MonotonicClock(), scenario="board_swing_rear", config_path=cfg_path)
    pipe = Pipeline(sup, bus, cfg, queue_dir=str(tmp_path / "q"))
    pipe.manager.camera = StubCamera()  # no ffmpeg needed
    sup.start()
    pipe.start()
    try:
        assert sup.status()["range"]["impl"] == "team"
        ready = wait_for(lambda: pipe.queue.ready(), timeout=30, what="an event in the queue")
        time.sleep(0.3)
        assert sup.status()["range"]["impl"] == "team" and not sup.status()["range"]["on_fallback"]
        d = pipe.queue.ready()[0]
        event = json.loads((d / "event.json").read_text())
        trace = json.loads((d / "trace.json").read_text())
    finally:
        pipe.stop()
        sup.stop()
    assert ready
    jsonschema.validate(event, json.loads((CONTRACTS / "event.schema.json").read_text()))
    jsonschema.validate(trace, json.loads((CONTRACTS / "sensor_trace.schema.json").read_text()))
    assert event["type"] in ("struck_by_near_miss", "struck_by_warning")
    assert event["metrics"]["sensor_id"] in ("R2", "R3")
    assert event["firmware"]["impls"]["range"] == "team", event["firmware"]
    assert set(trace["ranges"]) <= {"R2", "R3"} and trace["ranges"], "trace carries the team sensor ids"
    assert event["metrics"]["closing_rate_mps"] == pytest.approx(2.5, abs=0.3), "cm -> mm conversion is exact"
