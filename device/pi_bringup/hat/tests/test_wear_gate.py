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


def _tof_run(mm_series, impact_at=None):
    """Feed one reading every 0.05 s from worn; return the time OFF fires (or None)."""
    from hat.tof import WearDetector
    from hat import config as cfg

    d = WearDetector()
    now = 0.0
    while not d.wear_on:
        d.update(80, 0, now)
        now += 0.05
    t0 = now
    for i, mm in enumerate(mm_series):
        t = t0 + i * 0.05
        recent = impact_at is not None and 0 <= i * 0.05 - impact_at <= cfg.TOF_POST_IMPACT_GRACE_S
        d.update(mm, 0 if mm is not None else 7, t, impact_recent=recent)
        if not d.wear_on:
            return i * 0.05
    return None


def test_hat_pressed_down_after_a_fall_stays_worn():
    assert _tof_run([40] * 300, impact_at=0.0) is None  # 15 s at 40 mm, right after an impact


def test_same_reading_with_no_impact_is_still_hat_off():
    assert _tof_run([35] * 100) is not None


def test_no_echo_after_impact_is_still_hat_off():
    assert _tof_run([None] * 100, impact_at=0.0) is not None


def test_grace_expires_and_hat_on_table_goes_off():
    assert _tof_run([35] * 600, impact_at=0.0) is not None
