"""FAKE teammate range script (tests only): distances in CENTIMETRES, 0 = no echo, their own sensor names.

``--style json``: one JSON object per reading, ``{"sensor": "back_left", "dist_cm": 120.0}``.
``--style text``: one line per sweep, ``R2=120.0 R3=85.5`` (cm).
Synthetic: back_left 120 cm (every 10th reading 0), back_right 85.5 cm. ``--truth FILE`` plays a JSONL of
``{"t_s", "sensor_id", "range_mm"}`` (mm, null = no echo) in real time instead.
Failure drills: ``--crash-after N`` exits non-zero after N readings, ``--exit-now`` exits at once,
``--garbage`` prints junk.
"""
import argparse
import json
import sys
import time

NAMES = {"R2": "back_left", "R3": "back_right"}
REST = {"R2": 120.0, "R3": 85.5}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--style", choices=("json", "text"), default="json")
    ap.add_argument("--truth")
    ap.add_argument("--slot-ms", type=float, default=30.0)
    ap.add_argument("--crash-after", type=int, default=0)
    ap.add_argument("--exit-now", action="store_true")
    ap.add_argument("--garbage", action="store_true")
    a = ap.parse_args()
    if a.exit_now:
        sys.exit(0)
    truth = {}
    if a.truth:
        for ln in open(a.truth):
            r = json.loads(ln)
            truth.setdefault(r["sensor_id"], []).append((r["t_s"], r["range_mm"]))
    t0, n = time.monotonic(), 0
    cur = dict(REST)
    while True:
        n += 1
        if a.garbage:
            print("Segmentation fault (core dumped) ###", flush=True)
            time.sleep(0.01)
            continue
        sid = ("R2", "R3")[n % 2]
        if truth:
            now = time.monotonic() - t0
            mm = next((v for t, v in truth.get(sid, []) if t >= now), None)
            cur[sid] = 0.0 if mm is None else mm / 10.0
        else:
            cur[sid] = 0.0 if (sid == "R2" and n % 20 == 0) else REST[sid]
        if a.style == "json":
            print(json.dumps({"sensor": NAMES[sid], "dist_cm": round(cur[sid], 2)}), flush=True)
        elif sid == "R3":  # one text line per sweep, after both sensors were read
            print(f"R2={round(cur['R2'], 2)} R3={round(cur['R3'], 2)}", flush=True)
        if a.crash_after and n >= a.crash_after:
            sys.exit(2)
        time.sleep(max(0.0, t0 + n * a.slot_ms / 1000 - time.monotonic()))


main()
