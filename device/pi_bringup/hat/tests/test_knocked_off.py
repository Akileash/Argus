"""Hat-knocked-off path: ToF raw not-near run confirms a fall without tilt/stillness."""

from __future__ import annotations

import math

from hat.imu import FallImpactDetector
from hat.tests.test_imu_config_and_fall import DT, FF, SIDE, UP, Stream
from hat.wear_state import WearState

T0 = 1000.0
TOF_DT = 0.05


class Rig:
    """IMU stream + ToF stream sharing one clock. near_fn(t) -> True/False."""

    def __init__(self, near_fn, wear_s=3.0):
        self.wear = WearState(history_s=30.0)
        self.near_fn = near_fn
        self.det = FallImpactDetector(wear_state=self.wear)
        self.st = Stream(self.det, t0=T0)
        self._next_tof = T0 - wear_s
        # hat has been worn (dwell-filtered flag True) for the lead-in
        self.worn_until = 1e9

    def _tof_to(self, t):
        while self._next_tof <= t:
            near = self.near_fn(self._next_tof)
            self.wear.update(self._next_tof < self.worn_until, t=self._next_tof,
                             distance_mm=100 if near else None, near=near)
            self._next_tof += TOF_DT

    def push(self, v):
        self._tof_to(self.st.t)
        self.st.push(v)

    def hold(self, v, secs):
        for _ in range(int(secs / DT)):
            self.push(v)

    def spike(self, v, secs=0.03):
        self.hold(v, secs)

    def ramp(self, a, b, secs):
        n = max(1, int(secs / DT))
        for i in range(n):
            f = i / n
            self.push(tuple(x + (y - x) * f for x, y in zip(a, b)))

    @property
    def hits(self):
        return self.st.hits


LEAVE = T0 + 1.25   # hat leaves the head at freefall onset


def knock_prefix(rig, ff_s=0.12):
    rig.hold(UP, 1.2)
    rig.ramp(UP, FF, 0.04)
    rig.hold(FF, ff_s)
    rig.spike((0.0, 0.0, -3.0))     # hat hits the ground


def off_after_leave(t):
    return t < LEAVE


def test_knocked_off_landing_upright_and_bouncing_is_a_fall_without_tilt():
    rig = Rig(off_after_leave)
    knock_prefix(rig)
    for i in range(int(1.5 / DT)):   # bounces upright, never settles quietly
        rig.push((0.0, 0.0, -(1.0 + 1.2 * abs(math.sin(i * 0.15)))))
    assert len(rig.hits) == 1
    h = rig.hits[0]
    assert h.confidence == "high" and h.knocked_off and not h.suppressed
    assert h.onset_age_s >= 0.4   # event is stamped back to the fall onset, not detection time


def test_knocked_off_then_rolling_is_a_fall():
    rig = Rig(off_after_leave)
    knock_prefix(rig)
    for i in range(int(2.5 / DT)):   # rolls: orientation keeps changing
        a = i * 0.08
        rig.push((math.sin(a) * 1.4, math.cos(a) * 1.3, -0.4))
    assert len(rig.hits) == 1 and rig.hits[0].knocked_off


def test_knock_fires_well_before_helmet_off_dwell():
    rig = Rig(off_after_leave)
    knock_prefix(rig)
    t_impact = rig.st.t
    while not rig.hits and rig.st.t - t_impact < 3.0:
        rig.push(UP)
    assert rig.hits and rig.st.t - LEAVE < 1.5   # << 3 s HELMET OFF dwell


def test_hat_stays_on_with_tof_dropouts_during_impact_is_not_knock():
    # invalid/not-near for 0.3 s around the impact, then back on the head
    imp = LEAVE
    rig = Rig(lambda t: not (imp <= t < imp + 0.3))
    knock_prefix(rig)
    rig.hold(UP, 3.5)               # head upright afterwards, hat on
    assert rig.hits == []


def test_long_dropout_sequence_broken_by_a_near_reading_is_not_knock():
    rig = Rig(lambda t: not ((LEAVE <= t < LEAVE + 0.4) or (LEAVE + 0.45 <= t < LEAVE + 0.9)))
    knock_prefix(rig)
    rig.hold(UP, 3.5)
    assert rig.hits == []


def test_removal_by_hand_without_impact_is_not_a_fall():
    rig = Rig(lambda t: t < T0 + 1.0)
    rig.hold(UP, 1.0)
    for i in range(int(2.0 / DT)):   # lifted and carried: gentle motion only
        rig.push((0.1 * math.sin(i * 0.05), 0.2, -(1.0 + 0.3 * math.sin(i * 0.08))))
    rig.hold(SIDE, 3.0)
    assert rig.hits == []


def test_removed_by_hand_then_dropped_firmly_is_not_the_knock_path():
    # ToF went not-near 1.5 s BEFORE the drop: run predates fall onset
    rig = Rig(lambda t: t < T0 + 0.3)
    rig.hold(UP, 0.4)
    rig.hold((0.1, 0.1, -1.0), 0.9)
    rig.ramp(UP, FF, 0.04)
    rig.hold(FF, 0.12)
    rig.spike((0.0, 0.0, -3.0))
    rig.hold(UP, 3.5)               # lands upright, still → old rule rejects too
    assert rig.hits == []


def test_tof_unavailable_falls_back_to_existing_rule():
    # no ToF polls at all (ready False): gate denies → suppressed, never knock
    det = FallImpactDetector(wear_state=WearState(history_s=30.0))
    st = Stream(det, t0=T0)
    st.hold(UP, 1.2)
    st.ramp(UP, FF, 0.04)
    st.hold(FF, 0.12)
    st.spike((0.0, 0.0, -3.0))
    st.hold(UP, 3.5)
    assert all(h.suppressed for h in st.hits)
    assert not any(h.knocked_off for h in st.hits)


def test_stale_tof_does_not_confirm_knock():
    rig = Rig(lambda t: t < T0 + 0.5)          # near, then ToF thread stalls …
    rig.worn_until = 1e9
    rig._next_tof = T0 - 3.0
    # … feed polls only until T0+0.9 then nothing (stalled): last poll is near=False
    # but older than KNOCK_TOF_STALE_S by the time the impact ends
    rig.hold(UP, 0.9)
    rig._next_tof = 1e12                        # stop polling
    rig.hold(UP, 0.3)
    rig.ramp(UP, FF, 0.04)
    rig.hold(FF, 0.12)
    rig.spike((0.0, 0.0, -3.0))
    rig.hold(UP, 3.5)
    assert not any(h.knocked_off for h in rig.hits)


def test_one_event_per_knock():
    rig = Rig(off_after_leave)
    knock_prefix(rig)
    rig.hold(UP, 8.0)
    assert len(rig.hits) == 1
