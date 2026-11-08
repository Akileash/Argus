"""Wear gate + person-fall signature unit tests (no hardware)."""

from __future__ import annotations

import time

from hat.imu import FallImpactDetector, ImuSample
from hat.wear_state import WearState


def _sample(t: float, mag: float) -> ImuSample:
    # Put magnitude mostly on az for realism
    return ImuSample(t=t, ax=0.0, ay=0.0, az=mag, gx=0.0, gy=0.0, gz=0.0)


def test_wear_gate_allows_when_worn_beforehand():
    wear = WearState(history_s=10.0)
    t0 = time.perf_counter()
    for i in range(20):
        wear.update(True, t=t0 + i * 0.2)
    g = wear.gate_fall(t0 + 4.0, lookback_s=3.0, min_on_fraction=0.5)
    assert g.allow is True
    assert g.helmet_worn_at_onset is True
    assert g.on_fraction >= 0.5


def test_wear_gate_blocks_when_off_beforehand():
    wear = WearState(history_s=10.0)
    t0 = time.perf_counter()
    for i in range(20):
        wear.update(False, t=t0 + i * 0.2)
    g = wear.gate_fall(t0 + 4.0, lookback_s=3.0, min_on_fraction=0.5)
    assert g.allow is False
    assert g.reason in ("helmet_off_at_onset", "helmet_off_lookback")


def test_fall_emitted_when_worn():
    wear = WearState(history_s=10.0)
    t0 = time.perf_counter()
    for i in range(30):
        wear.update(True, t=t0 + i * 0.1)
    det = FallImpactDetector(wear_state=wear)
    # idle gravity
    t = t0 + 3.0
    for _ in range(5):
        det.push(_sample(t, 1.0))
        t += 0.005
    # freefall ~120 ms below FREEFALL_G
    for _ in range(30):
        out = det.push(_sample(t, 0.15))
        t += 0.005
        assert out is None or out.suppressed  # shouldn't complete yet
    # soft land near 1 g
    hit = None
    for _ in range(40):
        hit = det.push(_sample(t, 1.0))
        t += 0.005
        if hit is not None:
            break
    assert hit is not None
    assert hit.type == "fall"
    assert hit.suppressed is False
    assert hit.helmet_worn_at_onset is True


def test_fall_suppressed_when_helmet_off():
    wear = WearState(history_s=10.0)
    t0 = time.perf_counter()
    for i in range(30):
        wear.update(False, t=t0 + i * 0.1)
    det = FallImpactDetector(wear_state=wear)
    t = t0 + 3.0
    for _ in range(5):
        det.push(_sample(t, 1.0))
        t += 0.005
    for _ in range(30):
        det.push(_sample(t, 0.15))
        t += 0.005
    hit = None
    for _ in range(40):
        hit = det.push(_sample(t, 1.0))
        t += 0.005
        if hit is not None:
            break
    assert hit is not None
    assert hit.type == "fall"
    assert hit.suppressed is True
    assert det.suppressed_falls >= 1


def test_no_wear_state_still_allows_fall():
    """Backward-compat: detector without WearState still emits."""
    det = FallImpactDetector(wear_state=None)
    t = time.perf_counter()
    for _ in range(5):
        det.push(_sample(t, 1.0))
        t += 0.005
    for _ in range(30):
        det.push(_sample(t, 0.15))
        t += 0.005
    hit = None
    for _ in range(40):
        hit = det.push(_sample(t, 1.0))
        t += 0.005
        if hit is not None:
            break
    assert hit is not None
    assert hit.suppressed is False


def test_hard_hit_without_freefall_is_fall_not_impact():
    wear = WearState(history_s=10.0)
    t0 = time.perf_counter()
    for i in range(30):
        wear.update(True, t=t0 + i * 0.1)
    det = FallImpactDetector(wear_state=wear)
    t = t0 + 3.0
    for _ in range(5):
        det.push(_sample(t, 1.0))
        t += 0.005
    hit = det.push(_sample(t, 3.2))
    assert hit is not None
    assert hit.type == "fall"
    assert hit.suppressed is False
    assert hit.freefall_ms == 0.0

