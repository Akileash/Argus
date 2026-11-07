"""Struck-by detector: ego-compensated closing rate on the HC-SR04 array (spec §4.1).

Pure and deterministic (detectors/base.py): feed samples in timestamp order.

Per sensor i (unit beam vector u_i in the hat frame, see geometry.py):
 1. Range track: last ``track_len`` (4) valid ranges. Out-of-bounds / no-echo
    samples are dropped. A sample inconsistent with the track (> ``jump_m``
    from the track's prediction) starts a new track. A sensor with no track can
    *inherit* an adjacent sensor's track (beam hand-off) if its range is within
    ``handoff_tol_m`` of that track's predicted range and the track's last sample
    is at most ``handoff_max_gap_s`` old.
 2. v_meas = -LS slope of raw range vs time (positive = closing).
 3. v_hat from ImuPipeline (Madgwick + gravity removal + leaky integrator + ZUPT).
 4. v_ego along the beam. Implemented window-matched: the track also stores an
    ego-compensated range  c_k = c_{k-1} + (r_k - r_{k-1}) + (P_k - P_{k-1}) . u_k
    where P = integral of v_hat (hat frame). Then v_obj = -LS slope(c) and
    v_ego = v_meas - v_obj, i.e. the LS-weighted mean of v_hat . u_i over exactly
    the samples in the track (not one instantaneous value at the last sample).
 5. v_obj = v_meas - v_ego, with ``ego_discount_only`` (default on): a negative
    v_ego (wearer apparently moving *away* from the target) is clamped to 0, so
    ego-motion can only explain closing away, never add to it. Reason: the
    leaky integrator is a high-pass filter; after any deceleration it reports
    a phantom velocity in the opposite direction for ~tau, which would inflate
    v_obj exactly when the wearer stops in front of an obstacle (walk backward,
    stop at the wall). The relative closing rate v_meas is also the physically
    correct rate for time-to-contact when the wearer moves away.
 6. Rotation gate: |omega| > ``rotation_gate_dps`` resets all tracks and drops
    range samples while it holds.
 7. Trigger when range < 2.0 m AND v_obj > 1.5 m/s AND TTC < 1.3 s AND hat on,
    for ``confirm_sweeps`` (2) consecutive track updates.
 8. Suppression: v_ego > 0.6 * v_meas -> require v_obj > 2.0 m/s.
 9. TOP uses the same logic (vertical ego-motion is well observed).

Outputs: ``struck_by_warning`` on every confirmed update; one
``struck_by_near_miss`` per approach (first confirmation; an approach ends after
``approach_gap_s`` with no confirmation); one ``struck_by_contact`` if
|a| >= ``impact_g`` within ``contact_window_s`` after the near miss.

``haptic_request(t_ns)`` maps the most urgent live warning to a motor and pulse
rate (rear -> back, TOP -> top; TTC 1.3 s -> 4 Hz ... < 0.5 s -> continuous).
"""

from __future__ import annotations

import math
from collections import deque
from typing import Literal

from argus.contracts import EnvSample, ImuSample, Motor, RangeSample, SensorId, TofSample

from .base import Detection, DetectorState
from .ego_motion import ImuPipeline
from .geometry import adjacency, beam_vectors
from .orientation import Calibration

DEFAULTS: dict = {
    # range track (§4.1 step 1)
    "range_min_m": 0.03,
    "range_max_m": 2.5,
    "track_len": 4,
    "min_track_samples": 4,
    "jump_m": 0.35,
    "first_step_max_rate_mps": 4.0,
    "handoff_tol_m": 0.2,
    "handoff_max_gap_s": 0.25,
    "track_timeout_s": 0.5,
    # gates / trigger (§4.1 steps 6-8)
    "rotation_gate_dps": 60.0,
    "trigger_range_m": 2.0,
    "v_obj_min_mps": 1.5,
    "ttc_max_s": 1.3,
    "confirm_sweeps": 2,
    "suppress_ego_ratio": 0.6,
    "suppress_v_obj_min_mps": 2.0,
    "ego_compensation": True,
    "ego_discount_only": True,
    # events
    "approach_gap_s": 1.0,
    "impact_g": 6.0,
    "contact_window_s": 1.5,
    "saturation_g": 15.9,
    # haptics
    "haptic_hold_s": 0.3,
    "haptic_ttc_slow_s": 1.3,
    "haptic_slow_hz": 4.0,
    "haptic_ttc_continuous_s": 0.5,
    "haptic_fast_hz": 10.0,
}

HapticRequest = tuple[Motor, "float | Literal['continuous']"]


def _ls_slope(ts: list[float], ys: list[float]) -> float:
    n = len(ts)
    mt = sum(ts) / n
    my = sum(ys) / n
    num = 0.0
    den = 0.0
    for t, y in zip(ts, ys):
        dt = t - mt
        num += dt * (y - my)
        den += dt * dt
    return num / den if den > 0.0 else 0.0


class _Track:
    __slots__ = ("c", "consecutive", "handoffs", "id", "last_P", "r", "sensor_id", "sensors", "t")

    def __init__(self, tid: int, sensor_id: SensorId, t_s: float, r: float, P: tuple, maxlen: int):
        self.id = tid
        self.sensor_id = sensor_id
        self.t: deque[float] = deque([t_s], maxlen=maxlen)
        self.r: deque[float] = deque([r], maxlen=maxlen)
        self.c: deque[float] = deque([r], maxlen=maxlen)
        self.last_P = P
        self.consecutive = 0
        self.handoffs = 0
        self.sensors: list[SensorId] = [sensor_id]

    def predict(self, t_s: float) -> float:
        if len(self.t) < 2:
            return self.r[-1]
        ts = list(self.t)
        rs = list(self.r)
        slope = _ls_slope(ts, rs)
        mt = sum(ts) / len(ts)
        mr = sum(rs) / len(rs)
        return mr + slope * (t_s - mt)

    def append(self, sensor_id: SensorId, t_s: float, r: float, P: tuple, u: tuple) -> None:
        dP = (P[0] - self.last_P[0], P[1] - self.last_P[1], P[2] - self.last_P[2])
        ego_step = dP[0] * u[0] + dP[1] * u[1] + dP[2] * u[2]
        self.c.append(self.c[-1] + (r - self.r[-1]) + ego_step)
        self.t.append(t_s)
        self.r.append(r)
        self.last_P = P
        if sensor_id != self.sensor_id:
            self.handoffs += 1
            self.sensor_id = sensor_id
            self.sensors.append(sensor_id)


class StruckByDetector:
    """Detector protocol implementation for struck-by (spec §4.1)."""

    name = "struck_by"

    def __init__(self, cfg: dict | None = None, calibration: Calibration | None = None):
        cfg = dict(cfg or {})
        self.cfg = {**DEFAULTS, **cfg}
        c = self.cfg
        self.u = beam_vectors(cfg)
        self.adj = adjacency(cfg)
        self.imu = ImuPipeline(cfg, calibration)
        self._maxlen = int(c["track_len"])
        self._P = (0.0, 0.0, 0.0)  # integral of v_hat (hat frame), m
        self._last_imu_t: int | None = None
        self._tracks: dict[SensorId, _Track] = {}
        self._next_id = 1
        self.tracks_created = 0
        self.rotation_resets = 0
        self.candidate_log: deque[dict] = deque(maxlen=256)
        # approach / event state
        self._approach_active = False
        self._last_confirm_ns: int | None = None
        self._near_miss: Detection | None = None
        self._contact_done = False
        self._impact_peak: float | None = None
        self._impact_sat = False
        self._impact_t: int | None = None
        # haptics
        self._live: dict[SensorId, tuple[int, float]] = {}

    # ------------------------------------------------------------ IMU
    def on_imu(self, s: ImuSample, state: DetectorState) -> list[Detection]:
        c = self.cfg
        imu = self.imu
        was_cal = imu.calibrated
        imu.update(s)
        out: list[Detection] = []
        if not imu.calibrated:
            return out
        if was_cal and self._last_imu_t is not None:
            dt = (s.t_ns - self._last_imu_t) * 1e-9
            if 0.0 < dt <= 0.05:
                v = imu.v_hat
                P = self._P
                self._P = (P[0] + v[0] * dt, P[1] + v[1] * dt, P[2] + v[2] * dt)
        self._last_imu_t = s.t_ns

        if imu.omega_norm_dps > c["rotation_gate_dps"] and self._tracks:
            self._tracks.clear()
            self.rotation_resets += 1

        self._expire_approach(s.t_ns)
        self._check_contact(s, state, out)
        return out

    def _check_contact(self, s: ImuSample, state: DetectorState, out: list[Detection]) -> None:
        nm = self._near_miss
        if nm is None or self._contact_done:
            return
        c = self.cfg
        dt_ns = s.t_ns - nm.t_ns
        if dt_ns < 0:
            return
        in_window = dt_ns <= c["contact_window_s"] * 1e9
        a = math.sqrt(s.ax * s.ax + s.ay * s.ay + s.az * s.az)
        if in_window and a >= c["impact_g"]:
            if self._impact_peak is None:
                self._impact_t = s.t_ns
                self._impact_peak = a
            self._impact_peak = max(self._impact_peak, a)
            if max(abs(s.ax), abs(s.ay), abs(s.az)) >= c["saturation_g"]:
                self._impact_sat = True
            return  # wait for the pulse to end so peak_g is the pulse peak
        if self._impact_peak is not None:
            self._contact_done = True
            out.append(
                Detection(
                    t_ns=self._impact_t or s.t_ns,
                    type="struck_by_contact",
                    sensor_id=nm.sensor_id,
                    peak_g=self._impact_peak,
                    peak_g_saturated=self._impact_sat,
                    closing_rate_mps=nm.closing_rate_mps,
                    ttc_s=nm.ttc_s,
                    range_m=nm.range_m,
                    debug={
                        "near_miss_t_ns": nm.t_ns,
                        "impact_delay_s": ((self._impact_t or s.t_ns) - nm.t_ns) * 1e-9,
                        "hat_on": state.hat_on,
                    },
                )
            )
        elif not in_window:
            self._contact_done = True  # window expired without impact

    def _expire_approach(self, t_ns: int) -> None:
        if (
            self._approach_active
            and self._last_confirm_ns is not None
            and t_ns - self._last_confirm_ns > self.cfg["approach_gap_s"] * 1e9
        ):
            self._approach_active = False

    # ------------------------------------------------------------ ranges
    def on_range(self, s: RangeSample, state: DetectorState) -> list[Detection]:
        c = self.cfg
        if not self.imu.calibrated:
            return []
        t_ns = s.t_ns
        t_s = t_ns * 1e-9
        self._expire_approach(t_ns)
        # drop stale tracks
        timeout = c["track_timeout_s"]
        for sid in [k for k, tr in self._tracks.items() if t_s - tr.t[-1] > timeout]:
            del self._tracks[sid]
        if self.imu.omega_norm_dps > c["rotation_gate_dps"]:
            self._tracks.clear()
            return []
        if s.range_mm is None:
            return []
        r = s.range_mm / 1000.0
        if not (c["range_min_m"] <= r <= c["range_max_m"]):
            return []

        sid = s.sensor_id
        u = self.u[sid]
        P = self._P
        tr = self._tracks.get(sid)
        if tr is not None and not self._consistent(tr, t_s, r, c["jump_m"]):
            del self._tracks[sid]
            tr = None
        if tr is None:
            tr = self._find_handoff(sid, t_s, r)
            if tr is not None:
                del self._tracks[tr.sensor_id]
        if tr is None:
            tr = _Track(self._next_id, sid, t_s, r, P, self._maxlen)
            self._next_id += 1
            self.tracks_created += 1
        else:
            tr.append(sid, t_s, r, P, u)
        self._tracks[sid] = tr
        return self._evaluate(tr, t_ns, state)

    def _consistent(self, tr: _Track, t_s: float, r: float, tol: float) -> bool:
        if len(tr.t) == 1:
            dt = t_s - tr.t[-1]
            allow = max(tol, self.cfg["first_step_max_rate_mps"] * dt)
            return abs(r - tr.r[-1]) <= allow
        return abs(r - tr.predict(t_s)) <= tol

    def _find_handoff(self, sid: SensorId, t_s: float, r: float) -> _Track | None:
        c = self.cfg
        best: _Track | None = None
        best_err = c["handoff_tol_m"]
        for nb in self.adj.get(sid, ()):
            tr = self._tracks.get(nb)
            if tr is None or t_s - tr.t[-1] > c["handoff_max_gap_s"]:
                continue
            err = abs(r - tr.predict(t_s))
            if err <= best_err:
                best, best_err = tr, err
        return best

    def _evaluate(self, tr: _Track, t_ns: int, state: DetectorState) -> list[Detection]:
        c = self.cfg
        n = len(tr.t)
        if n < c["min_track_samples"]:
            tr.consecutive = 0
            return []
        ts = list(tr.t)
        v_meas = -_ls_slope(ts, list(tr.r))
        if c["ego_compensation"]:
            v_ego = v_meas + _ls_slope(ts, list(tr.c))  # = v_meas - (-slope(c))
        else:
            v_ego = 0.0
        v_ego_raw = v_ego
        if c["ego_discount_only"] and v_ego < 0.0:
            v_ego = 0.0
        v_obj = v_meas - v_ego
        rng = tr.r[-1]
        ttc = rng / v_obj if v_obj > 1e-6 else math.inf
        suppressed = v_meas > 0.0 and v_ego > c["suppress_ego_ratio"] * v_meas
        v_req = c["suppress_v_obj_min_mps"] if suppressed else c["v_obj_min_mps"]
        geom_ok = rng < c["trigger_range_m"] and v_obj > v_req and ttc < c["ttc_max_s"]
        cond = geom_ok and state.hat_on
        tr.consecutive = tr.consecutive + 1 if cond else 0

        v_hat = self.imu.v_hat
        u = self.u[tr.sensor_id]
        debug = {
            "track_id": tr.id,
            "sensor_id": tr.sensor_id,
            "track_sensors": list(tr.sensors),
            "handoffs": tr.handoffs,
            "n": n,
            "t_s": ts,
            "ranges_m": list(tr.r),
            "comp_ranges_m": list(tr.c),
            "v_meas_mps": v_meas,
            "v_ego_mps": v_ego,
            "v_ego_raw_mps": v_ego_raw,
            "v_ego_inst_mps": v_hat[0] * u[0] + v_hat[1] * u[1] + v_hat[2] * u[2],
            "v_obj_mps": v_obj,
            "ttc_s": ttc,
            "range_m": rng,
            "v_hat_mps": v_hat,
            "omega_dps": self.imu.omega_norm_dps,
            "zupt_active": self.imu.zupt_active,
            "suppressed": suppressed,
            "v_obj_required_mps": v_req,
            "condition": cond,
            "consecutive": tr.consecutive,
            "hat_on": state.hat_on,
        }
        if rng < c["trigger_range_m"] and v_meas > c["v_obj_min_mps"]:
            self.candidate_log.append({"t_ns": t_ns, **debug})

        out: list[Detection] = []
        if tr.consecutive < c["confirm_sweeps"]:
            return out
        self._live[tr.sensor_id] = (t_ns, ttc)
        self._last_confirm_ns = t_ns
        warn = Detection(
            t_ns=t_ns,
            type="struck_by_warning",
            sensor_id=tr.sensor_id,
            closing_rate_mps=v_obj,
            ttc_s=ttc,
            range_m=rng,
            debug=debug,
        )
        out.append(warn)
        if not self._approach_active:
            self._approach_active = True
            nm = Detection(
                t_ns=t_ns,
                type="struck_by_near_miss",
                sensor_id=tr.sensor_id,
                closing_rate_mps=v_obj,
                ttc_s=ttc,
                range_m=rng,
                debug=debug,
            )
            self._near_miss = nm
            self._contact_done = False
            self._impact_peak = None
            self._impact_sat = False
            self._impact_t = None
            out.append(nm)
        return out

    # ------------------------------------------------------------ unused streams
    def on_tof(self, s: TofSample, state: DetectorState) -> list[Detection]:
        return []

    def on_env(self, s: EnvSample, state: DetectorState) -> list[Detection]:
        return []

    # ------------------------------------------------------------ haptics
    def haptic_request(self, t_ns: int) -> HapticRequest | None:
        """Most urgent live warning -> (motor, pulse_hz | 'continuous'), else None."""
        c = self.cfg
        hold = c["haptic_hold_s"] * 1e9
        best: tuple[SensorId, float] | None = None
        for sid, (tw, ttc) in self._live.items():
            if 0 <= t_ns - tw <= hold and (best is None or ttc < best[1]):
                best = (sid, ttc)
        if best is None:
            return None
        sid, ttc = best
        motor: Motor = "top" if sid == "TOP" else "back"
        return motor, self.pulse_rate(ttc)

    def pulse_rate(self, ttc: float) -> float | Literal["continuous"]:
        c = self.cfg
        t_cont, t_slow = c["haptic_ttc_continuous_s"], c["haptic_ttc_slow_s"]
        if ttc < t_cont:
            return "continuous"
        if ttc >= t_slow:
            return float(c["haptic_slow_hz"])
        frac = (t_slow - ttc) / (t_slow - t_cont)
        return float(c["haptic_slow_hz"] + frac * (c["haptic_fast_hz"] - c["haptic_slow_hz"]))
