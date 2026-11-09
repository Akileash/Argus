"""Replay saved fall/impact imu_traces through OLD vs NEW fall logic.

  python3 -m hat.tools.replay_falls /path/events.jsonl [--since 2026-11-08T19:16]

Each logged event carries ~2 s of [t_ms, ax, ay, az] ending at the detection.
Traces are stitched on absolute time into contiguous segments (overlaps removed,
gaps > 0.5 s start a new segment) and each segment is fed to a fresh detector.

Columns per session (a session = events separated by < 10 min):
  logged   events of type fall/impact that carry a trace (what the hat reported)
  old      emits when the frozen legacy detector replays the stream
  new      emits of the new detector on the stream exactly as recorded
  pending  new detector saw a candidate (freefall/impact) but the trace ended
           before stillness/orientation could be judged (traces stop at the
           old detection, so post-impact data does not exist)
  blocked  seconds of stream the new detector refused (implausible magnitude)
  new_fix  new emits after dividing the post-glitch part by 8 (what the sensor
           would have said had it kept its +-16 g config); an estimate only
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import types
from datetime import datetime
from typing import Dict, List, Tuple

try:  # laptop without smbus2
    import smbus2  # noqa: F401
except ImportError:
    m = types.ModuleType("smbus2")
    m.SMBus = object
    sys.modules["smbus2"] = m

from hat.imu import FallImpactDetector, ImuSample  # noqa: E402
from hat.tools.legacy_fall_detector import LegacyFallDetector  # noqa: E402


def load(path: str, since: str):
    evs = []
    for line in open(path):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        tr = (e.get("sensor") or {}).get("imu_trace")
        if e.get("type") in ("fall", "impact") and tr and e["timestamp"] >= since:
            evs.append(e)
    evs.sort(key=lambda e: e["timestamp"])
    return evs


def stitch(evs) -> List[List[Tuple[float, float, float, float]]]:
    pts: Dict[int, Tuple[float, float, float, float]] = {}
    for e in evs:
        t0 = datetime.fromisoformat(e["timestamp"]).timestamp()
        for t_ms, ax, ay, az in e["sensor"]["imu_trace"]:
            t = t0 + t_ms / 1000.0
            pts[round(t * 1000)] = (t, ax, ay, az)  # 1 ms dedupe key
    ordered = [pts[k] for k in sorted(pts)]
    segs: List[list] = []
    for p in ordered:
        if segs and p[0] - segs[-1][-1][0] <= 0.5:
            segs[-1].append(p)
        else:
            segs.append([p])
    return segs


def mag(p):
    return math.sqrt(p[1] ** 2 + p[2] ** 2 + p[3] ** 2)


def break_index(seg) -> int:
    """First sample whose following 0.5 s has median |a| > 3 g (config glitch)."""
    m = [mag(p) for p in seg]
    for i in range(len(seg)):
        win = [x for x, p in zip(m[i:], seg[i:]) if p[0] - seg[i][0] <= 0.5]
        if len(win) >= 50 and sorted(win)[len(win) // 2] > 3.0:
            return i
    return len(seg)


def run(det_cls, seg, scale_after: int = -1):
    det = det_cls()
    hits, blocked_t, last = [], 0.0, None
    for i, (t, ax, ay, az) in enumerate(seg):
        k = 1.0 / 8.0 if 0 <= scale_after <= i else 1.0
        d = det.push(ImuSample(t, ax * k, ay * k, az * k, 0, 0, 0))
        if d is not None:
            hits.append((t, d))
        if getattr(det, "implausible", False) and last is not None:
            blocked_t += t - last
        last = t
    pending = getattr(det, "_state", "idle") in ("freefall", "wait_still")
    return hits, pending, blocked_t


def sessions(evs, gap_s=600):
    out, cur, prev = [], [], None
    for e in evs:
        t = datetime.fromisoformat(e["timestamp"]).timestamp()
        if prev is not None and t - prev > gap_s:
            out.append(cur)
            cur = []
        cur.append(e)
        prev = t
    if cur:
        out.append(cur)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("events")
    ap.add_argument("--since", default="2000")
    ap.add_argument("-v", "--verbose", action="store_true", help="list every new-logic emit")
    a = ap.parse_args()
    evs = load(a.events, a.since)
    print(f"{'session start':<20}{'span':>7}{'logged':>8}{'old':>6}{'new':>6}{'pending':>9}{'blocked_s':>10}{'new_fix':>9}")
    for sess in sessions(evs):
        segs = stitch(sess)
        o = n = nf = pend = 0
        blk = 0.0
        for seg in segs:
            if len(seg) < 20:
                continue
            ho, _, _ = run(LegacyFallDetector, seg)
            hn, p, b = run(FallImpactDetector, seg)
            hf, pf, _ = run(FallImpactDetector, seg, break_index(seg))
            o += len(ho)
            n += len(hn)
            nf += len(hf)
            pend += int(p or pf)
            blk += b
            if a.verbose:
                for t, d in hn:
                    print("   NEW", datetime.fromtimestamp(t).isoformat(), d.confidence, round(d.peak_accel_g, 2))
        span = (datetime.fromisoformat(sess[-1]["timestamp"]) - datetime.fromisoformat(sess[0]["timestamp"])).total_seconds()
        print(f"{sess[0]['timestamp'][:19]:<20}{span:>6.0f}s{len(sess):>8}{o:>6}{n:>6}{pend:>9}{blk:>10.1f}{nf:>9}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
