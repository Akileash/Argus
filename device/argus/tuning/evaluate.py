"""Evaluate thresholds on a directory of labelled recordings and format the scorecard."""

from __future__ import annotations

from pathlib import Path

from argus.tuning.runner import Recording, Thresholds, load_recording, run_recording
from argus.tuning.score import (
    DEFAULT_TOL_S,
    Labels,
    RecordingResult,
    Scorecard,
    aggregate,
    labels_path_for,
    load_labels,
    match_recording,
)

DEFAULT_RECORDINGS_DIR = Path(__file__).resolve().parents[2] / "recordings"

Dataset = list[tuple[Recording, Labels]]


def discover(directory: Path | str) -> tuple[list[Path], list[Path]]:
    """(labelled recordings, unlabelled recordings) under ``directory`` (recursive, sorted)."""
    labelled, unlabelled = [], []
    for p in sorted(Path(directory).rglob("*.jsonl")):
        (labelled if labels_path_for(p) else unlabelled).append(p)
    return labelled, unlabelled


def load_dataset(paths: list[Path]) -> Dataset:
    out: Dataset = []
    for p in paths:
        lab = load_labels(p)
        assert lab is not None, f"{p}: no labels sidecar"
        out.append((load_recording(p), lab))
    return out


def evaluate(dataset: Dataset, thresholds: Thresholds, tol_s: float = DEFAULT_TOL_S) -> Scorecard:
    results: list[RecordingResult] = []
    for rec, labels in dataset:
        dets = run_recording(rec, thresholds)
        results.append(match_recording(rec.name, labels, dets, rec.t0_ns, rec.duration_s, tol_s))
    return aggregate(results, tol_s)


# ------------------------------------------------------------------ formatting
def _f(x: float | None, fmt: str = "{:.2f}") -> str:
    return "-" if x is None else fmt.format(x)


def format_scorecard(sc: Scorecard, verbose: bool = True) -> str:
    head = ["type", "labelled", "detected", "TP", "miss", "FP", "FA/h", "rate", "med err s", "max err s", "lead s"]
    rows = [head]
    for ts in [*sc.per_type.values(), sc.overall]:
        r = ts.detection_rate
        rows.append([
            ts.type, str(ts.expected), str(ts.detections), str(ts.tp), str(ts.misses), str(ts.fp),
            f"{ts.fa_per_hour:.1f}", "-" if r is None else f"{r:.0%}",
            _f(ts.median_err_s, "{:+.2f}"), _f(ts.max_err_s, "{:+.2f}"), _f(ts.median_lead_s),
        ])
    w = [max(len(r[i]) for r in rows) for i in range(len(head))]
    lines = [f"{len(sc.recordings)} recordings, {sc.total_hours * 60:.1f} min, tolerance +-{sc.tol_s:.2f} s"]
    for i, r in enumerate(rows):
        lines.append("  ".join(c.ljust(w[j]) if j == 0 else c.rjust(w[j]) for j, c in enumerate(r)))
        if i == 0 or i == len(rows) - 2:
            lines.append("  ".join("-" * x for x in w))
    if verbose:
        bad = []
        for r in sc.recordings:
            miss = [m.expected.type for m in r.matches if m.detection is None]
            fps = [f"{d.type}@{t:.1f}s" for d, t in r.false_positives]
            if miss or fps:
                bad.append(f"  {r.name}: " + "; ".join(
                    ([f"MISSED {', '.join(miss)}"] if miss else []) + ([f"FALSE ALARM {', '.join(fps)}"] if fps else [])))
        lines.append("")
        lines.append("problems:" if bad else "no misses, no false alarms")
        lines += bad
    return "\n".join(lines)
