#!/usr/bin/env python3
"""Offline validation of wear-gated fall logic (simulated IMU + wear).

Trials (no hardware required):
  1. worn + freefall + soft land  → FALL
  2. off-hat + freefall/shake     → SUPPRESSED
  3. worn + walk-like ~1g noise   → no event
  4. worn + soft catch (0.14→1.05)→ FALL
  5. worn fall then wear OFF      → FALL still emitted (gate uses onset)

  cd ~/argus && source .venv/bin/activate
  python validate_wear_gated_fall.py
"""

from __future__ import annotations

import time

from hat.imu import FallImpactDetector, ImuSample
from hat.wear_state import WearState


def samp(t: float, mag: float) -> ImuSample:
    return ImuSample(t=t, ax=0.0, ay=0.0, az=mag, gx=0.0, gy=0.0, gz=0.0)


def feed_worn(wear: WearState, t0: float, seconds: float = 4.0, worn: bool = True) -> None:
    t = t0
    end = t0 + seconds
    while t <= end:
        wear.update(worn, t=t)
        t += 0.05


def run_soft_fall(det: FallImpactDetector, t: float) -> tuple:
    for _ in range(10):
        det.push(samp(t, 1.0))
        t += 0.005
    for _ in range(30):  # 150 ms freefall @ 0.15g
        det.push(samp(t, 0.15))
        t += 0.005
    hit = None
    for _ in range(50):
        hit = det.push(samp(t, 1.02))
        t += 0.005
        if hit is not None:
            break
    return hit, t


def main() -> int:
    results = []

    # 1) worn mat/soft fall
    wear = WearState()
    t0 = time.perf_counter()
    feed_worn(wear, t0, 4.0, True)
    det = FallImpactDetector(wear_state=wear)
    hit, _ = run_soft_fall(det, t0 + 4.0)
    ok = hit is not None and hit.type == "fall" and not hit.suppressed
    results.append(("worn + soft land → FALL", ok, hit))

    # 2) helmet off + same signature
    wear = WearState()
    t0 = time.perf_counter()
    feed_worn(wear, t0, 4.0, False)
    det = FallImpactDetector(wear_state=wear)
    hit, _ = run_soft_fall(det, t0 + 4.0)
    ok = hit is not None and hit.suppressed
    results.append(("off-hat + same IMU → SUPPRESSED", ok, hit))

    # 3) worn walk (no freefall)
    wear = WearState()
    t0 = time.perf_counter()
    feed_worn(wear, t0, 4.0, True)
    det = FallImpactDetector(wear_state=wear)
    t = t0 + 4.0
    hit = None
    for i in range(400):
        mag = 1.0 + 0.15 * ((i % 20) / 20.0)  # 1.0–1.15 g
        hit = det.push(samp(t, mag)) or hit
        t += 0.005
    ok = hit is None
    results.append(("worn + walk noise → no event", ok, hit))

    # 4) Fall1-style 0.14 → 1.05 while worn
    wear = WearState()
    t0 = time.perf_counter()
    feed_worn(wear, t0, 4.0, True)
    det = FallImpactDetector(wear_state=wear)
    t = t0 + 4.0
    for _ in range(10):
        det.push(samp(t, 1.0))
        t += 0.005
    for _ in range(25):
        det.push(samp(t, 0.14))
        t += 0.005
    hit = None
    for _ in range(40):
        hit = det.push(samp(t, 1.05))
        t += 0.005
        if hit is not None:
            break
    ok = hit is not None and hit.type == "fall" and not hit.suppressed
    results.append(("worn + 0.14g→1.05g soft catch → FALL", ok, hit))

    # 5) worn at onset; wear flips OFF after signature starts — still FALL
    wear = WearState()
    t0 = time.perf_counter()
    feed_worn(wear, t0, 4.0, True)
    det = FallImpactDetector(wear_state=wear)
    t = t0 + 4.0
    for _ in range(10):
        det.push(samp(t, 1.0))
        t += 0.005
    for _ in range(30):  # enter freefall state (> FREEFALL_MIN_S)
        det.push(samp(t, 0.15))
        t += 0.005
    # hat bounces off mid-fall (after onset) — must not erase the fall
    wear.update(False, t=t)
    hit = None
    for _ in range(40):
        hit = det.push(samp(t, 1.0))
        t += 0.005
        if hit is not None:
            break
    ok = hit is not None and hit.type == "fall" and not hit.suppressed
    results.append(("worn onset, OFF after → still FALL", ok, hit))

    print("=" * 64)
    print("WEAR-GATED FALL VALIDATION")
    print("=" * 64)
    all_ok = True
    for name, ok, hit in results:
        mark = "PASS" if ok else "FAIL"
        detail = ""
        if hit is not None:
            detail = (
                f"  type={hit.type} suppressed={hit.suppressed} "
                f"reason={hit.suppress_reason or '-'} "
                f"ff={hit.freefall_ms:.0f}ms"
            )
        print(f"  [{mark}] {name}{detail}")
        all_ok &= ok
    print("=" * 64)
    print("OVERALL:", "ALL PASS" if all_ok else "SOME FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
