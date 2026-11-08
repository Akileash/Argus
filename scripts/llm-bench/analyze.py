"""Score the raw outputs: schema validity (worker's assembleReport), fall correctness, hallucination flags, timings.

Usage: uv run --no-project python analyze.py   -> results/validation.jsonl, results/scores.json, prints markdown tables.
Heuristic keyword checks only; RESULTS.md notes the manual read of the outputs.
"""
import json
import re
import statistics as st
import subprocess
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAW = HERE / "results/raw"
FALL_VERB = re.compile(r"\b(fall|falls|fell|falling|drop|dropped|step(?:s|ped|ping)? (?:off|down|over|out)|jump|plunge)", re.I)
FALL_PLACE = re.compile(r"\b(ledge|edge|opening|stair|stairwell|hole|void|lower|below|drop-off|floor)", re.I)
# things that are NOT in the fall clip (or in the test pattern clip) -> candidate hallucinations
HALLU = re.compile(r"\b(forklift|truck|excavator|crane|vehicle|ladder|scaffold\w*|harness|vest|hi-vis|glove\w*|dog|child|woman|women|"
                   r"two (?:workers|people|men)|multiple (?:workers|people)|other (?:workers|people)|crowd|rebar|helmet|"
                   r"hard hat|safety glasses|machinery|equipment|concrete mixer|beam|pallet|board|plank)\b", re.I)
PERSON = re.compile(r"\b(person|people|worker|workers|man|men|woman|pedestrian|human)\b", re.I)


def validate(files: list[Path]) -> dict:
    out = subprocess.run(["pnpm", "exec", "tsx", "../scripts/llm-bench/validate.ts", *map(str, files)],
                         cwd=HERE.parent.parent / "worker", capture_output=True, text=True)
    res = {}
    for line in out.stdout.splitlines():
        if line.startswith("{"):
            j = json.loads(line)
            res[j["file"]] = j
    return res


def load(p: Path):
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def report_text(rep: dict) -> str:
    return json.dumps({k: rep.get(k) for k in ("summary", "sequence_of_events", "hazard", "causes", "confidence_and_gaps", "evidence")})


def fall_ok(rep: dict) -> tuple[bool, bool]:
    """(mentions step-off/fall + place, a fall-like sequence entry sits within [-1.5, +1.0] s of t0)"""
    txt = report_text(rep)
    mention = bool(FALL_VERB.search(txt) and FALL_PLACE.search(txt))
    timing = False
    for e in rep.get("sequence_of_events", []) or []:
        try:
            if FALL_VERB.search(str(e.get("description", ""))) and -1.5 <= float(e["offset_s"]) <= 1.0:
                timing = True
        except Exception:
            pass
    return mention, timing


def main() -> None:
    runs = [json.loads(l) for l in (HERE / "results/runs.jsonl").read_text().splitlines() if l.strip()]
    reports = sorted(RAW.glob("*.report.json"))
    val = validate(reports)
    (HERE / "results/validation.jsonl").write_text("\n".join(json.dumps(v) for v in val.values()) + "\n")
    rows = defaultdict(list)
    for r in runs:
        safe = r["model"].replace(":", "-")
        tag = f"{safe}__{r['case']}__run{r['run']}"
        rp, op = RAW / f"{tag}.report.json", RAW / f"{tag}.observations.json"
        rep, obs = load(rp), load(op)
        v = val.get(str(rp), {"valid": False})
        r["schema_valid"] = bool(v.get("valid"))
        r["schema_error"] = v.get("error")
        r["obs_valid"] = isinstance(obs, dict) and isinstance(obs.get("observations"), list)
        r["n_obs"] = len(obs["observations"]) if r["obs_valid"] else 0
        obs_txt = json.dumps(obs) if obs else ""
        rep_txt = report_text(rep) if isinstance(rep, dict) else ""
        r["hallu_terms"] = sorted({m.group(0).lower() for m in HALLU.finditer(obs_txt + " " + rep_txt)})
        if r["case"] == "fall" and isinstance(rep, dict):
            r["fall_mention"], r["fall_timing"] = fall_ok(rep)
        if r["case"] == "struck_by" and r["obs_valid"]:
            r["pattern_person_obs"] = len([o for o in obs["observations"] if PERSON.search(str(o.get("observation", "")))])
            r["contact_visible"] = obs.get("contact_visible")
        rows[(r["model"], r["case"])].append(r)
    (HERE / "results/scores.json").write_text(json.dumps([r for v in rows.values() for r in v], indent=1))

    def med(xs):
        xs = [x for x in xs if x is not None]
        return round(st.median(xs), 1) if xs else None

    print("| model | case | runs | cold total s | warm total s (median) | stage1 s | stage2 s | gen tok/s s1 / s2 | peak model GB | peak RSS GB | JSON valid (schema) | fall mention | fall timing |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for (m, c), rs in rows.items():
        cold = [r["total_wall_s"] for r in rs if r["cold"]]
        warm = [r["total_wall_s"] for r in rs if not r["cold"]]
        s1 = med([r.get("stage1", {}).get("wall_s") for r in rs if not r["cold"]])
        s2 = med([r.get("stage2", {}).get("wall_s") for r in rs if not r["cold"]])
        t1 = med([r.get("stage1", {}).get("gen_tok_s") for r in rs]); t2 = med([r.get("stage2", {}).get("gen_tok_s") for r in rs])
        valid = sum(r["schema_valid"] for r in rs)
        fm = sum(bool(r.get("fall_mention")) for r in rs) if c == "fall" else "-"
        ft = sum(bool(r.get("fall_timing")) for r in rs) if c == "fall" else "-"
        print(f"| {m} | {c} | {len(rs)} | {cold[0] if cold else '-'} | {med(warm)} | {s1} | {s2} | {t1} / {t2} | "
              f"{max(r['peak_ollama_model_gb'] for r in rs)} | {max(r['peak_ollama_rss_gb'] for r in rs)} | {valid}/{len(rs)} | {fm}/{len(rs) if c=='fall' else ''} | {ft}/{len(rs) if c=='fall' else ''} |")
    print()
    for (m, c), rs in rows.items():
        print(m, c, "hallu terms:", sorted({t for r in rs for t in r["hallu_terms"]}),
              "| pattern person obs:", [r.get("pattern_person_obs") for r in rs], "| contact_visible:", [r.get("contact_visible") for r in rs],
              "| errors:", [r.get("error") or r.get("schema_error") for r in rs if r.get("error") or r.get("schema_error")][:2])


if __name__ == "__main__":
    main()
