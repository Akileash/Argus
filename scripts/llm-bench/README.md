# llm-bench

Local Ollama benchmark for the Argus report worker. Measurement only; nothing here is wired into the product. Results and the recommendation are in RESULTS.md.

Pipeline mirrored from `worker/src/pipeline/run.ts` (hybrid): stage 1 vision model -> observations JSON, stage 2 writer -> report JSON. Both use Ollama's `format` JSON-schema option with the worker's real schemas and prompts.

## Reproduce

```bash
brew install ollama && ollama serve &            # or: brew services start ollama
ollama pull gemma3:12b qwen3-vl:8b-instruct qwen2.5vl:7b   # one per command

# 1. prompts, schemas and sensor summaries from the worker code (needs `pnpm install` at the repo root)
cd worker && pnpm exec tsx ../scripts/llm-bench/gen_context.ts

# 2. 30 s clips (scratch dir; not committed). Same padding as the mock camera: first frame held, real footage, last frame held.
D=/tmp/bench; mkdir -p $D
ffmpeg -y -i device/fixtures/clips/fall-from-ledge.mp4 -an -vf "fps=30,tpad=start_mode=clone:start_duration=19.2:stop_mode=clone:stop_duration=10,trim=duration=30,setpts=PTS-STARTPTS" -c:v libx264 -pix_fmt yuv420p $D/fall30.mp4
ffmpeg -y -f lavfi -i testsrc2=size=372x404:rate=30 -t 30 -c:v libx264 -pix_fmt yuv420p $D/pattern30.mp4

# 3. frames (dense around t0 = 20 s, a few spread over the clip)
cd scripts/llm-bench
python3 frames.py $D/fall30.mp4 $D/fr_fall
python3 frames.py $D/pattern30.mp4 $D/fr_struck_by

# 4. run (3 runs x 2 cases x 3 models is about 1.5 h); needs Python 3.10+, stdlib only
uv run --no-project python bench.py --models gemma3:12b qwen3-vl:8b-instruct qwen2.5vl:7b --runs 3 --frames-root $D

# 5. validate with the worker's assembleReport and score
uv run --no-project python analyze.py
```

`bench.py` default `--frames-root` is the scratch dir used for the published run; pass your own.

Files: `gen_context.ts` (dump worker prompts/schemas/sensor text), `frames.py` (sampling), `bench.py` (harness, timings, memory sampler), `validate.ts` (worker `assembleReport` validation), `analyze.py` (scores and tables), `inputs/context.json`, `results/` (raw outputs, `runs.jsonl`, `scores.json`, `validation.jsonl`).
