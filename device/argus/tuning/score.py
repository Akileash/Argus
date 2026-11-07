"""Match detections to labelled expectations and compute the scorecard metrics.

Label sidecar (``<recording>.labels.json``): ``{label, expected: [{type, t_s?, sensor?}], notes}``.
Extensions understood here: ``sensors`` (list of acceptable sensors) and ``t_window_s`` ([lo, hi]
acceptance window, used instead of ``t_s +- tol``; ``t_s`` is then only the reference for timing
error and warning lead time, defaulting to the window midpoint).
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from argus.detectors.base import Detection

EVENT_TYPES = (
    "struck_by_near_miss", "struck_by_contact", "head_impact", "fall", "hat_knocked_off", "hat_removed",
)
DEFAULT_TOL_S = 0.75
WARNING_LOOKBACK_S = 3.0
UPGRADE_PAIR_S = 2.0  # a contact this soon after a near_miss is the same event, upgraded
_NS = 1e9


@dataclass(frozen=True)
class Expected:
    type: str
    t_s: float | None = None
    sensors: tuple[str, ...] | None = None
    window: tuple[float, float] | None = None

    @property
    def ref_s(self) -> float | None:
        if self.t_s is not None:
            return self.t_s
        return None if self.window is None else (self.window[0] + self.window[1]) / 2


@dataclass(frozen=True)
class Labels:
    label: str = ""
    expected: tuple[Expected, ...] = ()
    notes: str = ""


def parse_labels(raw: dict[str, Any]) -> Labels:
    exp = []
    for e in raw.get("expected") or []:
        sens = e.get("sensors", e.get("sensor"))
        if isinstance(sens, str):
            sens = [sens]
        win = e.get("t_window_s")
        exp.append(Expected(
            type=e["type"],
            t_s=None if e.get("t_s") is None else float(e["t_s"]),
            sensors=tuple(sens) if sens else None,
            window=None if win is None else (float(win[0]), float(win[1])),
        ))
    return Labels(str(raw.get("label", "")), tuple(exp), str(raw.get("notes", "")))


def labels_path_for(recording: Path) -> Path | None:
    """``foo.jsonl.labels.json`` or ``foo.labels.json``."""
    for cand in (recording.with_name(recording.name + ".labels.json"), recording.with_suffix(".labels.json")):
        if cand.exists():
            return cand
    return None


def load_labels(recording: Path | str) -> Labels | None:
    p = labels_path_for(Path(recording))
    return None if p is None else parse_labels(json.loads(p.read_text()))


# ------------------------------------------------------------------ matching
@dataclass
class Match:
    expected: Expected
    detection: Detection | None
    t_det_s: float | None = None
    error_s: float | None = None  # detection - reference (signed)
    lead_s: float | None = None   # struck-by warning lead time


@dataclass
class RecordingResult:
    name: str
    label: str
    duration_s: float
    matches: list[Match] = field(default_factory=list)
    false_positives: list[tuple[Detection, float]] = field(default_factory=list)  # (detection, t_s)
    n_detections: dict[str, int] = field(default_factory=dict)


def _fits(e: Expected, d: Detection, t: float, tol: float) -> bool:
    if d.type != e.type:
        return False
    if e.sensors is not None and d.sensor_id not in e.sensors:
        return False
    if e.window is not None:
        return e.window[0] <= t <= e.window[1]
    if e.t_s is not None:
        return abs(t - e.t_s) <= tol
    return True


def match_recording(
    name: str, labels: Labels, detections: list[Detection], t0_ns: int, duration_s: float,
    tol_s: float = DEFAULT_TOL_S,
) -> RecordingResult:
    """One-to-one greedy matching, closest in time first. Unmatched event detections are false positives."""
    ev = sorted(((d, (d.t_ns - t0_ns) / _NS) for d in detections if d.type in EVENT_TYPES), key=lambda x: x[1])
    res = RecordingResult(name, labels.label, duration_s)
    for d, _ in ev:
        res.n_detections[d.type] = res.n_detections.get(d.type, 0) + 1

    pairs = []
    for ei, e in enumerate(labels.expected):
        for di, (d, t) in enumerate(ev):
            if _fits(e, d, t, tol_s):
                ref = e.ref_s
                pairs.append((abs(t - ref) if ref is not None else t, ei, di))
    pairs.sort()
    exp_used: dict[int, int] = {}
    det_used: set[int] = set()
    for _, ei, di in pairs:
        if ei not in exp_used and di not in det_used:
            exp_used[ei] = di
            det_used.add(di)

    # A near_miss that was upgraded to a contact is one event: if one half matched a label,
    # the other half is not a false positive.
    ignored: set[int] = set()
    for ci, (c, ct) in enumerate(ev):
        if c.type != "struck_by_contact":
            continue
        for ni, (n, nt) in enumerate(ev):
            if n.type == "struck_by_near_miss" and 0 <= ct - nt <= UPGRADE_PAIR_S:
                if ni in det_used and ci not in det_used:
                    ignored.add(ci)
                elif ci in det_used and ni not in det_used:
                    ignored.add(ni)

    warnings = sorted((d.t_ns - t0_ns) / _NS for d in detections if d.type == "struck_by_warning")
    for ei, e in enumerate(labels.expected):
        di = exp_used.get(ei)
        if di is None:
            res.matches.append(Match(e, None))
            continue
        d, t = ev[di]
        ref = e.ref_s
        m = Match(e, d, t, None if ref is None else t - ref)
        if e.type.startswith("struck_by") and ref is not None:
            prior = [w for w in warnings if ref - WARNING_LOOKBACK_S <= w <= ref]
            if prior:
                m.lead_s = ref - prior[0]
        res.matches.append(m)
    res.false_positives = [ev[i] for i in range(len(ev)) if i not in det_used and i not in ignored]
    return res


# ------------------------------------------------------------------ aggregation
@dataclass
class TypeScore:
    type: str
    expected: int = 0
    detections: int = 0
    tp: int = 0
    misses: int = 0
    fp: int = 0
    fa_per_hour: float = 0.0
    median_err_s: float | None = None
    max_err_s: float | None = None  # signed error with the largest magnitude
    median_lead_s: float | None = None

    @property
    def detection_rate(self) -> float | None:
        return self.tp / self.expected if self.expected else None

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "detection_rate": self.detection_rate}


@dataclass
class Scorecard:
    per_type: dict[str, TypeScore]
    overall: TypeScore
    total_hours: float
    recordings: list[RecordingResult]
    tol_s: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "tolerance_s": self.tol_s,
            "total_hours": self.total_hours,
            "overall": self.overall.to_dict(),
            "per_type": {k: v.to_dict() for k, v in self.per_type.items()},
            "recordings": [
                {
                    "name": r.name, "label": r.label, "duration_s": r.duration_s,
                    "misses": [m.expected.type for m in r.matches if m.detection is None],
                    "false_positives": [{"type": d.type, "t_s": round(t, 3), "sensor": d.sensor_id}
                                        for d, t in r.false_positives],
                }
                for r in self.recordings
            ],
        }


def _fill(ts: TypeScore, matches: list[Match], fps: int, detections: int, hours: float) -> None:
    ts.expected = len(matches)
    ts.tp = sum(m.detection is not None for m in matches)
    ts.misses = ts.expected - ts.tp
    ts.fp = fps
    ts.detections = detections
    ts.fa_per_hour = fps / hours if hours > 0 else 0.0
    errs = [m.error_s for m in matches if m.error_s is not None]
    if errs:
        ts.median_err_s = statistics.median(errs)
        ts.max_err_s = max(errs, key=abs)
    leads = [m.lead_s for m in matches if m.lead_s is not None]
    if leads:
        ts.median_lead_s = statistics.median(leads)


def aggregate(results: list[RecordingResult], tol_s: float = DEFAULT_TOL_S) -> Scorecard:
    hours = sum(r.duration_s for r in results) / 3600.0
    ms: dict[str, list[Match]] = defaultdict(list)
    fps: dict[str, int] = defaultdict(int)
    dets: dict[str, int] = defaultdict(int)
    for r in results:
        for m in r.matches:
            ms[m.expected.type].append(m)
        for d, _ in r.false_positives:
            fps[d.type] += 1
        for t, n in r.n_detections.items():
            dets[t] += n
    per_type: dict[str, TypeScore] = {}
    for t in EVENT_TYPES:
        if ms[t] or fps[t] or dets[t]:
            ts = TypeScore(t)
            _fill(ts, ms[t], fps[t], dets[t], hours)
            per_type[t] = ts
    overall = TypeScore("overall")
    _fill(overall, [m for r in results for m in r.matches], sum(fps.values()), sum(dets.values()), hours)
    return Scorecard(per_type, overall, hours, results, tol_s)
