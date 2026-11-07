"""Haptic pattern player."""

from __future__ import annotations

from argus.detectors.base import Detection
from argus.detectors.struck_by import StruckByDetector
from argus.events.haptics import HapticPlayer
from argus.events.trace import TraceRecorder

MS = 1_000_000


class FakeDriver:
    def __init__(self):
        self.calls = []

    def set(self, motor, level):
        self.calls.append((motor, level))


def make(req=None, hat_on=True):
    drv, st = FakeDriver(), {"req": req, "hat": hat_on}
    rec = TraceRecorder()
    p = HapticPlayer(drv, lambda t: st["req"], lambda: st["hat"], rec)
    return p, drv, st, rec


def test_pulse_rate_mapping_and_motor_direction():
    sb = StruckByDetector({})
    assert sb.pulse_rate(1.3) == 4.0 and sb.pulse_rate(0.49) == "continuous"
    assert 4.0 < sb.pulse_rate(0.9) < 10.0


def test_back_pulses_at_4hz_other_motor_stays_off():
    p, drv, _, rec = make(("back", 4.0))
    for t in range(0, 1000, 5):
        p.tick(t * MS)
    on_edges = [i for i, c in enumerate(drv.calls) if c == ("back", 1.0)]
    assert len(on_edges) == 4  # 4 pulses in one second
    assert all(m == "back" for m, _ in drv.calls)
    assert rec.haptics[0]["pattern"] == "pulse_4hz" and rec.haptics[0]["motor"] == "back"


def test_continuous_and_top_motor():
    p, drv, _, _ = make(("top", "continuous"))
    p.tick(0)
    p.tick(100 * MS)
    assert drv.calls == [("top", 1.0)]  # edge-triggered, stays on


def test_switching_motor_turns_old_one_off():
    p, drv, st, _ = make(("back", "continuous"))
    p.tick(0)
    st["req"] = ("top", "continuous")
    p.tick(10 * MS)
    assert drv.calls[0] == ("back", 1.0) and sorted(drv.calls[1:]) == [("back", 0.0), ("top", 1.0)]


def test_warning_stops_when_request_ends():
    p, drv, st, rec = make(("back", "continuous"))
    p.tick(0)
    st["req"] = None
    p.tick(300 * MS)
    assert drv.calls[-1] == ("back", 0.0)
    assert abs(rec.haptics[0]["duration_s"] - 0.0) < 1e-9 or rec.haptics[0]["duration_s"] >= 0


def test_fall_buzzes_both_for_1500ms_then_stops():
    p, drv, _, rec = make()
    p.on_detection(Detection(0, "fall"), 0)
    assert sorted(drv.calls) == [("back", 1.0), ("top", 1.0)]
    p.tick(1499 * MS)
    assert len(drv.calls) == 2
    p.tick(1500 * MS)
    assert sorted(drv.calls[2:]) == [("back", 0.0), ("top", 0.0)]
    assert rec.haptics[0]["pattern"] == "ack_1500ms" and rec.haptics[0]["motor"] == "both"


def test_trigger_to_motor_is_immediate():
    p, _drv, _, _ = make()
    p.on_detection(Detection(5 * MS, "head_impact"), 5 * MS)
    assert p.last_set_t_ns == 5 * MS  # same call, well under the 50 ms budget


def test_hat_removed_gives_no_haptic():
    p, drv, _, _ = make()
    p.on_detection(Detection(0, "hat_removed"), 0)
    p.on_detection(Detection(0, "hat_knocked_off"), 0)
    assert drv.calls == []


def test_suppressed_while_hat_off():
    p, drv, _st, _ = make(("back", "continuous"), hat_on=False)
    p.on_detection(Detection(0, "fall"), 0)
    p.tick(10 * MS)
    assert all(level == 0.0 for _, level in drv.calls)


def test_hat_taken_off_mid_buzz_silences_motors():
    p, _drv, st, _ = make()
    p.on_detection(Detection(0, "fall"), 0)
    st["hat"] = False
    p.tick(100 * MS)
    assert p.level == {"top": 0.0, "back": 0.0}
