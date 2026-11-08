"""Argus report-worker benchmark against a local Ollama server (stdlib only).

Mirrors the worker's hybrid pipeline: stage 1 = vision model -> observations JSON (OBSERVATION_SCHEMA),
stage 2 = writer -> report JSON (LLM_SCHEMA). Both constrained with Ollama's `format` = JSON schema.

Usage: python3 bench.py --models qwen3-vl:8b gemma3:12b --cases fall struck_by --runs 3
Inputs: inputs/context.json (gen_context.ts), frames in --frames-root/fr_<case>/ (frames.py).
Outputs: results/raw/<model>__<case>__run<N>.{observations,report}.json + results/runs.jsonl
"""
import argparse
import base64
import json
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OLLAMA = "http://127.0.0.1:11434"
NUM_CTX = 24576  # 16 frames cost ~17.7k prompt tokens on Qwen3-VL


def post(path: str, body: dict, timeout: int = 900) -> dict:
    req = urllib.request.Request(OLLAMA + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode()[:300]}") from None


def get(path: str) -> dict:
    with urllib.request.urlopen(OLLAMA + path, timeout=30) as r:
        return json.loads(r.read())


def unload_all() -> None:
    for m in get("/api/ps").get("models", []):
        post("/api/generate", {"model": m["name"], "keep_alive": 0})
    for _ in range(30):
        if not get("/api/ps").get("models"):
            return
        time.sleep(1)


class MemSampler(threading.Thread):
    """Samples /api/ps (model size, VRAM part) and summed RSS of ollama processes once a second."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.stop = threading.Event()
        self.peak_model_gb = 0.0
        self.peak_rss_gb = 0.0

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                size = sum(m.get("size", 0) for m in get("/api/ps").get("models", []))
                self.peak_model_gb = max(self.peak_model_gb, size / 1e9)
                out = subprocess.run(["ps", "-axo", "rss,comm"], capture_output=True, text=True).stdout
                rss = sum(int(l.split()[0]) for l in out.splitlines()[1:] if "ollama" in l.lower()) * 1024
                self.peak_rss_gb = max(self.peak_rss_gb, rss / 1e9)
            except Exception:
                pass
            self.stop.wait(1.0)


def fmt_off(s: float) -> str:
    return f"{'+' if s >= 0 else ''}{s:.1f}s"


def chat(model: str, system: str, user: str, schema: dict, images: list[str] | None = None) -> dict:
    msg = {"role": "user", "content": user}
    if images:
        msg["images"] = images
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, msg],
        "stream": False,
        "format": schema,
        "keep_alive": "15m",
        "options": {"temperature": 0.2, "num_ctx": NUM_CTX, "num_predict": 6144},
    }
    t = time.time()
    r = post("/api/chat", body)
    r["_wall_s"] = time.time() - t
    return r


def stats(r: dict) -> dict:
    pe, ev = r.get("prompt_eval_count", 0), r.get("eval_count", 0)
    ped, evd = r.get("prompt_eval_duration", 1) / 1e9, r.get("eval_duration", 1) / 1e9
    return {
        "wall_s": round(r["_wall_s"], 2),
        "load_s": round(r.get("load_duration", 0) / 1e9, 2),
        "prompt_tokens": pe, "gen_tokens": ev,
        "prompt_tok_s": round(pe / ped, 1) if ped > 0 else None,
        "gen_tok_s": round(ev / evd, 1) if evd > 0 else None,
    }


def try_json(text: str):
    try:
        return json.loads(text), None
    except Exception as e:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if m:
            try:
                return json.loads(m.group(1)), "fenced"
            except Exception:
                pass
        return None, str(e)[:200]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--cases", nargs="+", default=["fall", "struck_by"])
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--frames-root", default="/private/tmp/claude-501/bench")
    a = ap.parse_args()
    ctx = json.loads((HERE / "inputs/context.json").read_text())
    raw = HERE / "results/raw"
    raw.mkdir(parents=True, exist_ok=True)
    log = HERE / "results/runs.jsonl"

    for model in a.models:
        safe = model.replace(":", "-").replace("/", "-")
        print(f"=== {model}: unloading all, first run is COLD", flush=True)
        unload_all()
        for case in a.cases:
            c = ctx["cases"][case]
            pre = c["pre_s"]
            fdir = Path(a.frames_root) / f"fr_{case}"
            frames = json.loads((fdir / "frames.json").read_text())
            images = [base64.b64encode((fdir / f["file"]).read_bytes()).decode() for f in frames]
            for run in range(1, a.runs + 1):
                tag = f"{safe}__{case}__run{run}"
                cold = case == a.cases[0] and run == 1
                mem = MemSampler(); mem.start()
                rec = {"model": model, "case": case, "run": run, "cold": cold}
                t_all = time.time()
                try:
                    # ---- stage 1: vision
                    listing = "\n".join(f"Image {i + 1}: clip time {f['clip_time_s']:.2f} s (offset {fmt_off(f['offset_s'])} from t0)"
                                        for i, f in enumerate(frames))
                    obs_prompt = (
                        f"Event type: {c['event_type']}. The trigger moment t0 is {pre} s into this clip (clip length about {pre + c['post_s']} s). "
                        f"You are given {len(frames)} frames sampled from the clip, in time order, dense around t0:\n{listing}\n"
                        "Describe what you can see, paying particular attention around t0. Give video_time_s as the clip time of the frame(s) you describe."
                    )
                    r1 = chat(model, ctx["observe_system"], obs_prompt, ctx["observation_schema"], images)
                    obs_text = r1["message"]["content"]
                    obs, e1 = try_json(obs_text)
                    rec["stage1"] = stats(r1)
                    rec["stage1_json_valid"] = obs is not None and e1 is None
                    (raw / f"{tag}.observations.json").write_text(obs_text)
                    # ---- stage 2: writer
                    if isinstance(obs, dict):
                        lines = []
                        for o in sorted(obs.get("observations", []), key=lambda o: o.get("video_time_s", 0)):
                            try:
                                lines.append(f"- {fmt_off(float(o['video_time_s']) - pre)} [{o.get('category')}] {o.get('observation')}")
                            except Exception:
                                pass
                        contact, vis = obs.get("contact_visible", "unclear"), obs.get("visibility_notes") or "none"
                    else:
                        lines, contact, vis = [], "unclear", "none"
                    p2 = "\n".join([
                        c["prompt_text"], "",
                        "Visual observations extracted from the clip by a video model (offsets relative to t0; facts only, no conclusions):",
                        *(lines or ["- (no observations returned)"]),
                        f"Physical contact with the hat visible or audible in the video: {contact}.",
                        f"Video visibility notes: {vis}", "",
                        'Write the investigation analysis as JSON. Mark facts from the observations above as source "video".',
                    ])
                    r2 = chat(model, ctx["writer_system"], p2, ctx["llm_schema"])
                    rep_text = r2["message"]["content"]
                    rep, e2 = try_json(rep_text)
                    rec["stage2"] = stats(r2)
                    rec["stage2_json_parse_ok"] = rep is not None and e2 is None
                    (raw / f"{tag}.report.json").write_text(rep_text)
                except Exception as e:  # keep going; record the failure
                    rec["error"] = f"{type(e).__name__}: {e}"[:300]
                mem.stop.set(); mem.join()
                rec["total_wall_s"] = round(time.time() - t_all, 2)
                rec["peak_ollama_model_gb"] = round(mem.peak_model_gb, 2)
                rec["peak_ollama_rss_gb"] = round(mem.peak_rss_gb, 2)
                with log.open("a") as fh:
                    fh.write(json.dumps(rec) + "\n")
                print(json.dumps(rec), flush=True)


if __name__ == "__main__":
    main()
