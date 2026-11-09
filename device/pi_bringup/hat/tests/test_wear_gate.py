"""Wear gate + person-fall signature unit tests (no hardware)."""

from __future__ import annotations

import time

from hat.imu import FallImpactDetector, ImuSample
from hat.tests.test_imu_config_and_fall import SIDE, UP, Stream, real_fall
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


def _worn_wear(on: bool, t0: float) -> WearState:
    wear = WearState(history_s=30.0)
    for i in range(200):
        wear.update(on, t=t0 + i * 0.05)
    return wear


def test_fall_emitted_when_worn():
    t0 = time.perf_counter()
    st = Stream(FallImpactDetector(wear_state=_worn_wear(True, t0)), t0=t0 + 2.0)
    real_fall(st)
    assert len(st.hits) == 1
    hit = st.hits[0]
    assert hit.type == "fall"
    assert hit.suppressed is False
    assert hit.helmet_worn_at_onset is True


def test_fall_suppressed_when_helmet_off():
    t0 = time.perf_counter()
    det = FallImpactDetector(wear_state=_worn_wear(False, t0))
    st = Stream(det, t0=t0 + 2.0)
    real_fall(st)
    assert len(st.hits) == 1
    assert st.hits[0].suppressed is True
    assert det.suppressed_falls >= 1


def test_no_wear_state_still_allows_fall():
    """Backward-compat: detector without WearState still emits."""
    st = Stream(FallImpactDetector(wear_state=None))
    real_fall(st)
    assert len(st.hits) == 1
    assert st.hits[0].suppressed is False


def test_hard_hit_without_freefall_needs_stillness_and_tilt():
    """A bare spike is not a fall; a hard hit then lying still re-oriented is."""
    t0 = time.perf_counter()
    det = FallImpactDetector(wear_state=_worn_wear(True, t0))
    st = Stream(det, t0=t0 + 2.0)
    st.hold(UP, 1.0)
    st.push((0.0, 0.0, -3.2))
    assert st.hits == []
    st.hold(SIDE, 1.0)  # no free fall, >=3 g, ~90 deg: the hit path accepts it
    assert len(st.hits) == 1 and st.hits[0].freefall_ms == 0.0
