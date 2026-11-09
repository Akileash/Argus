"""Replay / simulation harness for the ultrasonic near-miss rule (laptop only, no hardware).

  python3 -m hat.tools.replay_us                 # synthetic scenarios, OLD rule vs NEW rule
  python3 -m hat.tools.replay_us --events PATH   # also analyse logged line_of_fire events.jsonl
  python3 -m hat.tools.replay_us --csv rec.csv   # replay an ARGUS_US_RECORD recording (old vs new)

What the logged data can and cannot prove
  events.jsonl only holds the moments the OLD rule fired (range + closing speed at that instant).
  The range stream between triggers was never saved, so the logs cannot be re-run through a new
  rule. They can (a) show the targets were static (same range every time), (b) bound the noise,
  (c) say which fired events the new rule would necessarily have rejected. Everything else here is
  synthetic, with the noise model calibrated so the OLD rule reproduces the real fire rate on a
  static wall.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from collections import deque
from datetime import datetime
from typing import Callable, Deque, List, Optional, Tuple

from hat import config as cfg
from hat.ultrasonic import ApproachDetector, MotionState

PING_S = 0.080  # one sensor, alternating pings 40 ms apart
IMU_DT = 0.005

# Noise model (see calibrate_static()): Gaussian + rare outliers + dropouts.
SIGMA_M = 0.012
OUTLIER_P = 0.03
OUTLIER_SIGMA_M = 0.07
DROPOUT_P = 0.03


class LegacyDetector:
    """The rule that shipped before 2026-11-08, constants inlined. Two-sample difference."""

    WARN_D, WARN_C, NEAR_D, NEAR_C, DROP, COOL, BREAK, MAXV = 1.80, 0.40, 0.70, 0.15, 0.12, 1.5, 0.20, 8.0

    def __init__(self, side: str = "x"):
        self.hist: Deque[Tuple[float, float]] = deque(maxlen=2)
        self.last_good: Optional[float] = None
        self.cool = 0.0

    def update(self, t: float, raw: Optional[float], motion=None):
        if raw is None:
            if self.last_good is not None and t - self.last_good > self.BREAK:
                self.hist.clear(); self.last_good = None
            return None
        if self.last_good is not None and t - self.last_good > self.BREAK:
            self.hist.clear()
        if self.hist:
            tp, dp = self.hist[-1]
            if t > tp and abs(dp - raw) / (t - tp) > self.MAXV:
                return None
        self.hist.append((t, raw)); self.last_good = t
        if len(self.hist) < 2 or t < self.cool:
            return None
        (t0, d0), (t1, d1) = self.hist
        dt = t1 - t0
        if dt < 0.02:
            return None
        c = (d0 - d1) / dt
        drop = d0 - d1
        reason = None
        if d1 <= self.WARN_D and c >= self.WARN_C:
            reason = "close_and_fast"
        elif d1 <= self.NEAR_D and c >= self.NEAR_C:
            reason = "very_near"
        elif d1 <= self.WARN_D and drop >= self.DROP and c > 0:
            reason = "instant_drop"
        if reason is None:
            return None
        self.cool = t + self.COOL
        return (reason, round(c, 2))


class Scenario:
    def __init__(self, name: str, duration: float, rng_fn: Callable[[float], Optional[float]],
                 gyro_fn: Callable[[float], float] = lambda t: 0.0,
                 dyn_fn: Callable[[float], float] = lambda t: 0.0,
                 impact_range: Optional[float] = None, extra_dropout: float = 0.0,
                 should_fire: Optional[bool] = None, note: str = ""):
        self.name, self.duration, self.rng_fn = name, duration, rng_fn
        self.gyro_fn, self.dyn_fn = gyro_fn, dyn_fn
        self.impact_range, self.extra_dropout = impact_range, extra_dropout
        self.should_fire, self.note = should_fire, note


def _noisy(r: Optional[float], rnd: random.Random, extra_dropout: float) -> Optional[float]:
    if r is None or r > cfg.US_MAX_RANGE_M or r < cfg.US_MIN_RANGE_M:
        return None
    if rnd.random() < DROPOUT_P + extra_dropout:
        return None
    r += rnd.gauss(0, SIGMA_M)
    if rnd.random() < OUTLIER_P:
        r += rnd.gauss(0, OUTLIER_SIGMA_M)
    return max(r, 0.0)


def run(sc: Scenario, make: Callable[[], object], seed: int, use_motion: bool = True):
    """Return list of (t, reason, speed) fires and t_impact (first t truth range <= impact_range)."""
    rnd = random.Random(seed)
    det = make()
    motion = MotionState()
    t_imu = 0.0
    fires = []
    t_imp = None
    n = int(sc.duration / PING_S)
    for i in range(n):
        t = 1.0 + i * PING_S + rnd.uniform(-0.003, 0.003)
        while t_imu <= t:
            g = sc.gyro_fn(t_imu - 1.0)
            # accel is a magnitude only: dyn_fn(t) = signed deviation of |a| from 1 g
            a = 1.0 + sc.dyn_fn(t_imu - 1.0) + rnd.gauss(0, 0.01)
            motion.push(t_imu, 0.0, 0.0, a, g + rnd.gauss(0, 2.0), 0.0, 0.0)
            t_imu += IMU_DT
        truth = sc.rng_fn(t - 1.0)
        if sc.impact_range is not None and t_imp is None and truth is not None and truth <= sc.impact_range:
            t_imp = t
        ev = det.update(t, _noisy(truth, rnd, sc.extra_dropout), motion if use_motion else None)
        if ev is not None:
            if hasattr(ev, "reason"):
                ev = (ev.reason, ev.closing_speed_mps)
            fires.append((t - 1.0, ev[0], ev[1]))
        if t_imp is not None and sc.impact_range is not None and t > t_imp + 0.3:
            break
    return fires, (None if t_imp is None else t_imp - 1.0)


# ---------------------------------------------------------------- scenarios
def _bounce(amp: float, hz: float = 1.9):
    return lambda t: amp * math.sin(2 * math.pi * hz * t)


def scenarios() -> List[Scenario]:
    out: List[Scenario] = []
    out.append(Scenario("static wall 0.78 m, standing", 120.0, lambda t: 0.78, should_fire=False))
    out.append(Scenario("static wall 0.78 m, nodding head (gyro 15 dps)", 120.0, lambda t: 0.78,
                        gyro_fn=lambda t: 15.0 * math.sin(2 * math.pi * 0.7 * t), should_fire=False))
    for v in (0.5, 1.2):
        out.append(Scenario(
            f"walk BACKWARD into wall at {v} m/s", 4.0 / v + 1.0,
            lambda t, v=v: max(3.0 - v * max(t - 0.5, 0.0), 0.1),
            gyro_fn=lambda t: 12.0 * math.sin(2 * math.pi * 1.9 * t),
            dyn_fn=_bounce(0.15), should_fire=False,
            note="wearer moving: bar raised to 2.0 m/s"))
    # wall with normal 60 deg off the start heading at 1.2 m; beam sweeps toward it
    for dps, T in ((90.0, 0.67), (30.0, 2.0)):
        def r(t, dps=dps, T=T):
            th = min(max(t - 0.5, 0.0) * dps, 60.0)
            return 1.2 / math.cos(math.radians(60.0 - th))
        def g(t, dps=dps, T=T):
            return dps if 0.5 <= t < 0.5 + 60.0 / dps else 0.0
        out.append(Scenario(f"head turn {int(dps)} dps past wall (2.4 -> 1.2 m)", 0.5 + 60 / dps + 1.0,
                            r, gyro_fn=g, should_fire=False))
    # person crossing the beam at 1.0 m lateral offset, 1.4 m/s; echo only inside +-15 deg cone
    def crossing(t):
        x = -2.5 + 1.4 * t
        return math.hypot(1.0, x) if abs(x) < 1.0 * math.tan(math.radians(15)) else None
    out.append(Scenario("person walking ACROSS beam at 1 m", 4.0, crossing, should_fire=False))
    out.append(Scenario("person walking TOWARD wearer from behind 1.4 m/s", 3.0,
                        lambda t: 3.0 - 1.4 * t, impact_range=0.4, should_fire=True,
                        note="wearer standing"))
    out.append(Scenario("hand approach 0.5 m/s from 1.5 m", 3.0, lambda t: 1.5 - 0.5 * t,
                        impact_range=0.3, should_fire=False, note="slow; intentionally not a near-miss"))
    for v in (3.0, 4.5, 6.0):
        out.append(Scenario(f"object swung at head {v} m/s, wearer standing", 1.6,
                            lambda t, v=v: 3.0 - v * max(t - 0.3, 0.0), impact_range=0.15,
                            extra_dropout=0.2, should_fire=True))
    out.append(Scenario("object swung at head 4.5 m/s, wearer walking", 1.6,
                        lambda t: 3.0 - 4.5 * max(t - 0.3, 0.0), impact_range=0.15,
                        gyro_fn=lambda t: 12.0 * math.sin(2 * math.pi * 1.9 * t), dyn_fn=_bounce(0.15),
                        extra_dropout=0.2, should_fire=True))
    return out


def evaluate(sc: Scenario, make, seeds: int, use_motion: bool = True):
    nfire_runs = 0
    total = 0
    leads: List[float] = []
    for s in range(seeds):
        fires, t_imp = run(sc, make, 1000 + s, use_motion)
        total += len(fires)
        if fires:
            nfire_runs += 1
            if t_imp is not None:
                leads.append(t_imp - fires[0][0])
    return nfire_runs / seeds, total, (statistics.median(leads) if leads else None)


def print_table(seeds: int = 40) -> None:
    print(f"{'scenario':58s} {'want':>5s} | {'OLD fire%':>9s} {'lead':>6s} | {'NEW fire%':>9s} {'lead':>6s}")
    for sc in scenarios():
        old = evaluate(sc, LegacyDetector, seeds, use_motion=False)
        new = evaluate(sc, lambda: ApproachDetector("x"), seeds, use_motion=True)
        want = {True: "FIRE", False: "quiet", None: "-"}[sc.should_fire]
        fo = lambda e: f"{e[2]:.2f}s" if e[2] is not None else "-"
        if sc.duration >= 100:
            o = f"{old[1]/seeds/sc.duration*60:5.1f}/min"; n = f"{new[1]/seeds/sc.duration*60:5.1f}/min"
        else:
            o = f"{old[0]*100:7.0f}%"; n = f"{new[0]*100:7.0f}%"
        print(f"{sc.name:58s} {want:>5s} | {o:>9s} {fo(old):>6s} | {n:>9s} {fo(new):>6s}")
    print("lead = median time from first buzz to head impact (truth range 0.15 m); '/min' rows = fires per minute.")


def calibrate_static(seeds: int = 5) -> Tuple[float, float]:
    """Old-rule fires per second per sensor on a static 0.78 m wall under the noise model."""
    sc = Scenario("static", 120.0, lambda t: 0.78)
    tot = sum(run(sc, LegacyDetector, 7 + s, False)[0].__len__() for s in range(seeds))
    return tot / (seeds * sc.duration), 117 / 446.0


# ---------------------------------------------------------------- real logs
def analyse_events(path: str) -> None:
    ev = [json.loads(l) for l in open(path)]
    lof = [e for e in ev if e.get("type") == "line_of_fire" and e["timestamp"] >= "2026-11-08T19:16"]
    ts = [datetime.fromisoformat(e["timestamp"]).timestamp() for e in lof]
    d = [e["sensor"]["distance_m"] for e in lof]
    c = [e["sensor"]["closing_speed_mps"] for e in lof]
    span = ts[-1] - ts[0]
    print(f"\nLogged test session: {len(lof)} line_of_fire events in {span:.0f} s "
          f"({len(lof)/span*60:.0f}/min)")
    cl = [(dd, cc, e["side"]) for dd, cc, e in zip(d, c, lof) if 0.70 <= dd <= 0.85 and cc < 2.0]
    print(f"  static-wall cluster (0.70-0.85 m, <2 m/s): {len(cl)} events ({len(cl)/len(lof):.0%}); "
          f"range sd per side: "
          + ", ".join(f"{s}={statistics.pstdev([x for x,_,ss in cl if ss==s])*100:.1f} cm" for s in ("left", "right")))
    print(f"  closing speed: median {statistics.median(c):.2f}, <1.0 m/s: {sum(x<1.0 for x in c)}, "
          f">=2 m/s: {sum(x>=2 for x in c)}, >=3 m/s: {sum(x>=3 for x in c)}")
    print(f"  implied single-step range change at 80 ms (closing*0.08): median {statistics.median(c)*8:.1f} cm")
    both = 0
    for i, t in enumerate(ts):
        if any(abs(t - u) < 0.25 and e2["side"] != lof[i]["side"] for u, e2 in zip(ts, lof)):
            both += 1
    print(f"  events with the OTHER side also firing within 0.25 s: {both} ({both/len(lof):.0%})")
    # necessary conditions of the NEW rule
    def new_possible(dd, cc):
        if cc >= cfg.US_FAST_MPS and dd <= cfg.US_FAST_RANGE_M and dd / cc <= cfg.US_FAST_TTC_MAX_S:
            return True
        return cc >= cfg.US_MIN_OBJ_MPS and dd <= cfg.US_WARN_DISTANCE_M and dd / cc <= cfg.US_TTC_MAX_S
    keep = sum(new_possible(dd, cc) for dd, cc in zip(d, c))
    print(f"  events that could still satisfy the new rule's speed/TTC bars: {keep} of {len(lof)}; "
          f"{len(lof)-keep} are rejected for certain. The {keep} survivors also need 4 monotonic samples "
          f"+ no head rotation, which the log cannot show.")
    below = sum(dd < cfg.US_IGNORE_BELOW_M for dd in d)
    print(f"  events below {cfg.US_IGNORE_BELOW_M} m (own body / brim): {below}")


def replay_csv(path: str) -> None:
    rows = list(csv.DictReader(open(path)))
    for name, make, um in (("OLD", LegacyDetector, False), ("NEW", lambda s: ApproachDetector(s), True)):
        dets = {}
        motion = MotionState()
        n = 0
        for r in rows:
            t = float(r["t"])
            dets.setdefault(r["side"], make(r["side"]))
            if "gyro_dps" in r and r["gyro_dps"] != "":
                motion.push(t, 0, 0, 1.0 + (float(r["dyn_rms_g"]) if r.get("dyn_rms_g") else 0.0),
                            float(r["gyro_dps"]), 0, 0)
            raw = float(r["raw_m"] if "raw_m" in r else r["distance_m"]) if (r.get("raw_m") or r.get("distance_m")) else None
            if dets[r["side"]].update(t, raw, motion if um else None) is not None:
                n += 1
        print(f"{name}: {n} fires over {float(rows[-1]['t'])-float(rows[0]['t']):.0f} s")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events")
    ap.add_argument("--csv")
    ap.add_argument("--seeds", type=int, default=40)
    a = ap.parse_args(argv)
    mdl, real = calibrate_static()
    print(f"Noise model sigma={SIGMA_M*100:.1f} cm, outliers {OUTLIER_P:.0%} x {OUTLIER_SIGMA_M*100:.0f} cm, "
          f"dropouts {DROPOUT_P:.0%}")
    print(f"Calibration: OLD rule on a static wall fires {mdl:.2f}/s/sensor in the model vs {real:.2f}/s/sensor logged.\n")
    print_table(a.seeds)
    if a.events:
        analyse_events(a.events)
    if a.csv:
        replay_csv(a.csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
