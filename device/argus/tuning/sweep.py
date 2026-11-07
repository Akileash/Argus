"""Grid search over threshold values, scored on labelled recordings.

Objective (explicit, no ML): among grid points whose false alarms per hour are at most
``max_fa_per_hour``, maximise the detection rate (true positives / labelled events); break ties by
the larger median struck-by warning lead time, then fewer false alarms, then the smallest change from
the current config. Grid points that exceed the false-alarm budget rank after all feasible ones.
"""

from __future__ import annotations

import itertools
import multiprocessing as mp
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from argus.tuning.evaluate import Dataset, evaluate, load_dataset
from argus.tuning.runner import Thresholds, with_overrides

PARALLEL_MIN_POINTS = 6


def parse_param(spec: str) -> tuple[str, list[Any]]:
    """``struck_by.ego_tau_s=1.0,1.5,2.0`` -> (key, [1.0, 1.5, 2.0]) with YAML scalar parsing."""
    key, sep, vals = spec.partition("=")
    if not sep or "." not in key or not vals.strip():
        raise ValueError(f"bad --param {spec!r}; expected section.key=v1,v2,...")
    return key.strip(), [yaml.safe_load(v.strip()) for v in vals.split(",")]


def grid(params: dict[str, list[Any]]) -> list[dict[str, Any]]:
    keys = list(params)
    return [dict(zip(keys, combo, strict=True)) for combo in itertools.product(*params.values())]


@dataclass
class SweepResult:
    values: dict[str, Any]
    detection_rate: float
    fa_per_hour: float
    lead_s: float | None
    tp: int
    expected: int
    fp: int
    n_changed: int = 0
    feasible: bool = True


def _summarise(values: dict[str, Any], sc: Any, base: Thresholds, max_fa: float) -> SweepResult:
    o = sc.overall
    changed = sum(1 for k, v in values.items() if base[k.split(".")[0]][k.split(".", 1)[1]] != v)
    return SweepResult(values, o.detection_rate or 0.0, o.fa_per_hour, o.median_lead_s, o.tp, o.expected, o.fp,
                       changed, o.fa_per_hour <= max_fa)


def rank_key(r: SweepResult) -> tuple:
    return (not r.feasible, -r.detection_rate, -(r.lead_s if r.lead_s is not None else -1e9), r.fa_per_hour,
            r.n_changed)


_W: dict[str, Any] = {}


def _init_worker(paths: list[str], base: Thresholds, tol_s: float, max_fa: float) -> None:
    _W.update(dataset=load_dataset([Path(p) for p in paths]), base=base, tol=tol_s, max_fa=max_fa)


def _eval_point(values: dict[str, Any]) -> SweepResult:
    th = with_overrides(_W["base"], values)
    return _summarise(values, evaluate(_W["dataset"], th, _W["tol"]), _W["base"], _W["max_fa"])


def sweep(
    paths: list[Path], base: Thresholds, params: dict[str, list[Any]], max_fa_per_hour: float = 1.0,
    tol_s: float = 0.75, workers: int | None = None, dataset: Dataset | None = None,
) -> list[SweepResult]:
    """Evaluate every grid point; return results best first. ``workers=1`` forces serial."""
    points = grid(params)
    for p in points:
        with_overrides(base, p)  # validates keys early
    nproc = workers if workers is not None else min(os.cpu_count() or 1, len(points))
    if nproc > 1 and len(points) >= PARALLEL_MIN_POINTS:
        with mp.get_context("spawn").Pool(nproc, _init_worker, ([str(p) for p in paths], base, tol_s, max_fa_per_hour)) as pool:
            results = pool.map(_eval_point, points, chunksize=1)
    else:
        ds = dataset if dataset is not None else load_dataset(paths)
        results = [_summarise(p, evaluate(ds, with_overrides(base, p), tol_s), base, max_fa_per_hour) for p in points]
    return sorted(results, key=rank_key)


# ------------------------------------------------------------------ output
def yaml_snippet(values: dict[str, Any]) -> str:
    """Exact YAML to paste under ``thresholds:`` in config.yaml."""
    by_section: dict[str, dict[str, Any]] = {}
    for k, v in values.items():
        s, _, key = k.partition(".")
        by_section.setdefault(s, {})[key] = v
    lines = ["thresholds:"]
    for s, kv in by_section.items():
        lines.append(f"  {s}:")
        lines += [f"    {k}: {yaml.safe_dump(v, default_flow_style=True).splitlines()[0]}" for k, v in kv.items()]
    return "\n".join(lines)


def format_results(results: list[SweepResult], top: int, max_fa: float) -> str:
    keys = list(results[0].values)
    head = ["#", *[k.split(".", 1)[1] for k in keys], "rate", "TP/lab", "FP", "FA/h", "lead s", "ok"]
    rows = [head]
    for i, r in enumerate(results[:top], 1):
        rows.append([str(i), *[str(r.values[k]) for k in keys], f"{r.detection_rate:.0%}", f"{r.tp}/{r.expected}",
                     str(r.fp), f"{r.fa_per_hour:.1f}", "-" if r.lead_s is None else f"{r.lead_s:.2f}",
                     "yes" if r.feasible else "NO"])
    w = [max(len(r[i]) for r in rows) for i in range(len(head))]
    out = [f"{len(results)} grid points; objective: max detection rate with FA/h <= {max_fa:g}, tie-break lead time"]
    for i, r in enumerate(rows):
        out.append("  ".join(c.rjust(w[j]) for j, c in enumerate(r)))
        if i == 0:
            out.append("  ".join("-" * x for x in w))
    return "\n".join(out)


# ------------------------------------------------------------------ --apply
def apply_to_config(config_path: Path | str, values: dict[str, Any]) -> None:
    """Rewrite only the given ``thresholds.<section>.<key>`` scalars in place, keeping comments/layout."""
    p = Path(config_path)
    lines = p.read_text().splitlines(keepends=True)
    in_thr = False
    section: str | None = None
    done: set[str] = set()
    for i, line in enumerate(lines):
        if re.match(r"^\S", line):
            in_thr = line.startswith("thresholds:")
            section = None
            continue
        if not in_thr:
            continue
        m = re.match(r"^  (\w+):\s*(#.*)?$", line)
        if m:
            section = m.group(1)
            continue
        m = re.match(r"^(\s{4}(\w+):\s*)([^#\n]*?)(\s*(#.*)?)(\r?\n?)$", line)
        if m and section and f"{section}.{m.group(2)}" in values:
            key = f"{section}.{m.group(2)}"
            new = yaml.safe_dump(values[key], default_flow_style=True).splitlines()[0]
            lines[i] = f"{m.group(1)}{new}{m.group(4)}{m.group(6)}"
            done.add(key)
    missing = set(values) - done
    if missing:
        raise KeyError(f"not found in {p}: {sorted(missing)}")
    p.write_text("".join(lines))
