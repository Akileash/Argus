#!/usr/bin/env python3
"""
Analyze hard-hat IMU events from events.jsonl (or a pasted JSON file).

Reads detector output and prints:
  - a clear label (FALL / IMPACT / HAT REMOVED / NEAR FALL)
  - why it was classified that way
  - severity for a safety report
  - a short narrative an LLM can reuse

Usage:
  cd ~/argus && source .venv/bin/activate
  python analyze_events.py                  # ~/argus/events.jsonl
  python analyze_events.py events.jsonl
  python analyze_events.py --last 3
  python analyze_events.py --json-out report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ARGUS_DIR = Path(__file__).resolve().parent
DEFAULT_EVENTS = ARGUS_DIR / "events.jsonl"

LABELS = {
    "fall": "FALL",
    "impact": "IMPACT",
    "hat_removed": "HAT REMOVED",
    "near_fall": "NEAR FALL",
}

SEVERITY = {
    "fall": "CRITICAL",
    "hat_removed": "WARNING",
    "impact": "ATTENTION",
    "near_fall": "INFO",
}


def load_events(path: Path) -> List[dict]:
    if not path.exists():
        raise FileNotFoundError(f"No event file at {path}")
    events: List[dict] = []
    with path.open() as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            # Allow lines that start with "EVENT " from a pasted monitor log
            if line.startswith("EVENT "):
                line = line[6:].strip()
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"WARNING: skip line {lineno}: {e}", file=sys.stderr)
    return events


def _sensor(event: dict) -> dict:
    return event.get("sensor") or {}


def explain(event: dict) -> Dict[str, Any]:
    """Turn one detector event into a structured analysis."""
    etype = event.get("type", "unknown")
    s = _sensor(event)
    peak_g = float(s.get("peak_accel_g", 0) or 0)
    peak_gyro = float(s.get("peak_gyro_dps", 0) or 0)
    freefall_ms = float(s.get("freefall_ms", 0) or 0)
    tilt = float(s.get("tilt_change_deg", 0) or 0)
    still = bool(s.get("still_after", False))
    score = float(s.get("score", 0) or 0)
    set_down = bool(event.get("hat_possibly_set_down", False))
    worn_before = bool(event.get("worn_before", False))
    worn_after = bool(event.get("worn_after", False))

    reasons: List[str] = []
    if peak_g >= 3.0:
        reasons.append(f"accel peak {peak_g:.1f} g crossed the 3.0 g impact gate")
    if peak_gyro >= 300:
        reasons.append(f"high rotation ({peak_gyro:.0f} dps)")
    if freefall_ms >= 100:
        reasons.append(f"free-fall lasting {freefall_ms:.0f} ms before impact")
    elif freefall_ms > 0:
        reasons.append(f"brief low-g dip ({freefall_ms:.0f} ms) — not enough alone for free-fall")
    if tilt >= 60:
        reasons.append(f"gravity direction shifted {tilt:.0f}° (hat orientation changed a lot)")
    elif tilt >= 20:
        reasons.append(f"moderate orientation change ({tilt:.0f}°)")
    if still:
        reasons.append("motion settled afterward (still)")
    else:
        reasons.append("motion continued afterward (not still)")
    if worn_before and not worn_after:
        reasons.append("worn signal went from worn → not worn")
    if set_down:
        reasons.append("pattern matches hat set down on a surface (flagged, not discarded)")

    # Human label + safety take
    label = LABELS.get(etype, etype.upper())
    severity = SEVERITY.get(etype, "INFO")
    if set_down and etype == "impact":
        label = "IMPACT (likely set down)"
        severity = "INFO"

    if etype == "fall":
        verdict = "Treat as a fall / hard impact requiring attention."
        action = "Check on the wearer; include in the safety report as a high-priority incident."
    elif etype == "hat_removed":
        verdict = "Hat was likely knocked off or removed (big tilt + then still)."
        action = "Confirm whether the worker is OK and whether PPE is back on."
    elif set_down:
        verdict = "Looks like the hat was placed down, not a body fall."
        action = "Log as low priority; do not page as an emergency."
    elif etype == "near_fall":
        verdict = "Impact-like spike but the wearer/board kept moving — recovery or handling."
        action = "Note as a near-miss / handling event; no emergency page."
    elif etype == "impact":
        verdict = "Significant impact without a full fall signature."
        action = "Review context; escalate if worn_before was true and priority is medium/high."
    else:
        verdict = "Unrecognized type."
        action = "Inspect raw JSON."

    # Saturation note (IMU +/-16 g)
    notes: List[str] = []
    if peak_g >= 15.5:
        notes.append("Accel saturated near ±16 g — true peak was at least this hard.")
    if freefall_ms < 50 and etype == "fall":
        notes.append("Little/no free-fall; classified from impact size + rotation + stillness (tumbling-style).")
    if event.get("priority") == "low" and etype == "fall":
        notes.append("Unusual: fall with low priority — check detector weights.")

    narrative = (
        f"At {event.get('timestamp', '?')}, hat {event.get('hat_id', '?')} recorded a "
        f"{label} (priority={event.get('priority')}, confidence={event.get('confidence')}, "
        f"score={score:.2f}). Peak accel {peak_g:.1f} g, peak gyro {peak_gyro:.0f} dps, "
        f"free-fall {freefall_ms:.0f} ms, tilt change {tilt:.0f}°, still_after={still}. "
        f"{verdict}"
    )

    return {
        "event_id": event.get("event_id"),
        "timestamp": event.get("timestamp"),
        "label": label,
        "type": etype,
        "severity": severity,
        "priority": event.get("priority"),
        "confidence": event.get("confidence"),
        "score": score,
        "peak_accel_g": peak_g,
        "peak_gyro_dps": peak_gyro,
        "freefall_ms": freefall_ms,
        "tilt_change_deg": tilt,
        "still_after": still,
        "hat_possibly_set_down": set_down,
        "worn_before": worn_before,
        "worn_after": worn_after,
        "reasons": reasons,
        "verdict": verdict,
        "recommended_action": action,
        "notes": notes,
        "narrative": narrative,
    }


def print_report(analyses: List[dict]) -> None:
    if not analyses:
        print("No events to analyze.")
        return

    counts: Dict[str, int] = {}
    for a in analyses:
        counts[a["label"]] = counts.get(a["label"], 0) + 1

    print("=" * 72)
    print(f"ARGUS EVENT ANALYSIS — {len(analyses)} event(s)")
    print("=" * 72)
    print("Summary:", ", ".join(f"{k}×{v}" for k, v in sorted(counts.items())))
    print()

    for i, a in enumerate(analyses, 1):
        print("-" * 72)
        print(f"[{i}] {a['label']:24s}  severity={a['severity']:8s}  "
              f"priority={a['priority']}  conf={a['confidence']}")
        print(f"    time     {a['timestamp']}")
        print(f"    id       {a['event_id']}")
        print(
            f"    metrics  peak={a['peak_accel_g']:.1f} g  "
            f"gyro={a['peak_gyro_dps']:.0f} dps  "
            f"freefall={a['freefall_ms']:.0f} ms  "
            f"tilt={a['tilt_change_deg']:.0f}°  "
            f"still={a['still_after']}  score={a['score']:.2f}"
        )
        print(f"    worn     before={a['worn_before']}  after={a['worn_after']}  "
              f"set_down={a['hat_possibly_set_down']}")
        print("    why")
        for r in a["reasons"]:
            print(f"      • {r}")
        print(f"    verdict  {a['verdict']}")
        print(f"    action   {a['recommended_action']}")
        for n in a["notes"]:
            print(f"    note     {n}")
        print(f"    story    {a['narrative']}")
    print("-" * 72)

    # Safety-report style rollup
    critical = [a for a in analyses if a["severity"] == "CRITICAL"]
    warnings = [a for a in analyses if a["severity"] == "WARNING"]
    print()
    print("SAFETY ROLLUP")
    if critical:
        print(f"  CRITICAL falls/impacts: {len(critical)}")
        for a in critical:
            print(f"    - {a['timestamp']}: {a['peak_accel_g']:.1f} g, score {a['score']:.2f}")
    else:
        print("  CRITICAL falls/impacts: 0")
    if warnings:
        print(f"  WARNING (hat removed): {len(warnings)}")
    print(f"  Near-falls / handling: {sum(1 for a in analyses if a['type'] == 'near_fall')}")
    print(f"  Likely set-downs: {sum(1 for a in analyses if a['hat_possibly_set_down'])}")


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Analyze Argus IMU event JSONL")
    p.add_argument(
        "path",
        nargs="?",
        default=str(DEFAULT_EVENTS),
        help=f"events.jsonl path (default: {DEFAULT_EVENTS})",
    )
    p.add_argument("--last", type=int, default=0, help="Only analyze the last N events")
    p.add_argument(
        "--json-out",
        type=str,
        default="",
        help="Also write structured analysis JSON to this path (under ~/argus recommended)",
    )
    args = p.parse_args(argv)

    path = Path(args.path).expanduser()
    try:
        events = load_events(path)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if args.last > 0:
        events = events[-args.last :]

    analyses = [explain(e) for e in events]
    print_report(analyses)

    if args.json_out:
        out = Path(args.json_out).expanduser()
        # Keep writes inside ~/argus unless user gave an absolute path elsewhere
        if not out.is_absolute():
            out = ARGUS_DIR / out
        out.write_text(json.dumps(analyses, indent=2) + "\n")
        print(f"\nWrote {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
