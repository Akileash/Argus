# Local Ollama vs cloud APIs for the Argus report worker

Measured 2026-11-08 on an Apple M4 Pro, 24 GB unified memory, macOS, Ollama 0.40.15 (Homebrew). Nothing here is integrated into the product. The harness is in this folder (see README.md); every raw model output is in `results/raw/`, per-run timings in `results/runs.jsonl`, scored rows in `results/scores.json`, schema validation in `results/validation.jsonl`.

## TL;DR

- **Local works, but it is not good enough to be the default report writer.** The best local model (Qwen3-VL 8B instruct) got the main story right in 2 of 3 fall runs (worker steps off a raised edge and falls, step-off at about -0.8 s). It still picked the wrong hazard category every time (`falling_object`, never `fall_from_height`), invented fall-protection and supervision causes, and once looped into 6,144 tokens of garbage in stage 1.
- **Speed is acceptable, memory is the cost.** A warm two-stage run takes 80 to 130 s (the spec's cloud estimate is 1.5 to 3 min). The Ollama process holds 11 to 14 GB resident while it runs, on a laptop that also runs Supabase, the web app and the worker for the demo.
- **Recommendation: cloud by default (Gemini 3.5 Flash observes, Claude writes), local as an offline / outage fallback, not the other way round.** Details and the decision matrix are below.

## 1. Models used

Names verified on ollama.com/library (tags pages) before pulling. Sizes are what `ollama list` reports.

| Model (Ollama tag) | Role | Download size | Notes |
|---|---|---|---|
| `qwen3-vl:8b-instruct` | vision + writer | 6.2 GB | Q4_K_M. The plain `qwen3-vl:8b` tag is a thinking model: with a JSON `format` its thinking ate the whole token budget and returned empty content, so it was dropped and deleted. |
| `gemma3:12b` | vision + writer | 8.2 GB | Q4_K_M, 256 tokens per image. |
| `qwen2.5vl:7b` | vision + writer | 6.0 GB | Q4_K_M. |

Total on disk: 20.4 GB. All three stay downloaded and the Ollama server is left running. A text-only second-stage writer was not tested (see "Not measured").

## 2. Measured local results

Setup, identical for all models: Ollama `/api/chat`, `format` = the worker's real JSON schemas (`OBSERVATION_SCHEMA` for stage 1, `LLM_SCHEMA` for stage 2, dumped from `worker/src/llm-schema.ts`), the worker's real `observe_system.md` and `writer_system.md`, the worker's real `eventPromptText` sensor summary, `temperature 0.2`, `num_ctx 24576`, `num_predict 6144`. 16 frames per case: 13 frames from t0 -0.9 s to +0.9 s (6.7 fps) plus frames at -15, -6 and +6 s, each labelled with its clip time and offset from t0 in the prompt, JPEG at the source resolution (372x404; the clip is smaller than 640 px). 3 runs per model and case. The first case of each model was started after unloading every model, so run 1 is cold.

Cases:
- **fall**: the real `device/fixtures/clips/fall-from-ledge.mp4`, padded to 30 s exactly the way the mock camera does (first frame held for 19.2 s, real footage for 1.5 s, last frame held), so the step-off is at -0.8 s and the free fall starts at about t0 (t0 = 20.0 s). Sensor trace: `makeTrace('fall')`. Ground truth (looked at the frames): a man in a blue shirt, jeans and sandals walks to the edge of a raised floor and steps off into a lower opening / stairwell; cardboard boxes on the floor; no hard hat, no other people.
- **struck_by**: ffmpeg `testsrc2` test pattern (30 s) plus `makeTrace('struck_by_near_miss')`. Nothing real is visible; the right behaviour is to say so.

### Timing, memory, validity (3 runs per row)

| Model | Case | Cold total (run 1) | Warm total, median | Stage 1 (vision), warm median | Stage 2 (writer), warm median | Gen tok/s (stage 1 / 2) | Peak Ollama RSS | Report passes worker validation | Stage 1 valid JSON (both cases, 6 runs) |
|---|---|---|---|---|---|---|---|---|---|
| gemma3:12b | fall | 164 s | 130 s | 40 s | 90 s | 23.5 / 23.4 | 12.2 GB | 3/3 | 6/6 |
| gemma3:12b | struck_by | - | 113 s | 36 s | 77 s | 22.8 / 23.1 | 13.3 GB | 3/3 | |
| qwen3-vl:8b-instruct | fall | 425 s (*) | 80 s | 34 s | 47 s | 25.1 / 33.6 | 13.9 GB | 3/3 | 5/6 |
| qwen3-vl:8b-instruct | struck_by | - | 81 s | 37 s | 45 s | 23.6 / 33.4 | 13.3 GB | 3/3 | |
| qwen2.5vl:7b | fall | 340 s (*) | 78 s | 23 s | 55 s | 29.5 / 37.9 | 11.0 GB | 3/3 | 5/6 |
| qwen2.5vl:7b | struck_by | - | 92 s | 27 s | 52 s | 29.5 / 36.6 | 10.9 GB | 3/3 | |

Notes on the table:
- (*) The cold totals are dominated by a failed stage 1, not by loading. In both Qwen runs 1 the vision stage hit the 6,144-token cap (about 390 s for Qwen3-VL) with output that stops being valid JSON. The real load cost is in `runs.jsonl` (`load_s`): 3 to 5 s from a cold start for every model (weights come from the page cache after the first pull, so a truly cold boot with a first disk read would be slower). Gemma's cold run (164 s vs 130 s warm) is the cleanest cold-vs-warm figure.
- "Report passes worker validation" means the stage 2 output went through the worker's real `assembleReport` (zod plus `contracts/report.schema.json` via Ajv). All 18 stage 2 outputs pass, because Ollama enforces the JSON schema by grammar. The only way to get invalid JSON is a stage 1 loop, and the pipeline then writes the report from sensors alone (as the worker would on a failed video stage).
- Prompt tokens: Qwen3-VL costs about 1,100 tokens per image whatever the input size (17.7k tokens for 16 frames, about 105 s of prefill at about 168 tok/s on a cold prompt); Gemma 3 costs 256 per image (5.2k tokens). Repeated runs show huge `prompt_tok_s` figures because Ollama reuses the prompt cache; use the stage wall times, not those figures.
- Memory: "Peak Ollama RSS" is the summed resident size of all `ollama` processes, sampled once a second. `/api/ps` reported the loaded model plus KV cache at 8.2 GB (Gemma) and 8.8 GB (Qwen3-VL). Its reading for Qwen2.5-VL (2.8 GB) is not credible and should be ignored. All models ran 100% on the GPU. That leaves roughly 10 GB of the 24 GB for everything else (the demo stack: Supabase in Docker, Next.js web app, worker, Python device); this subtraction is an estimate, not a measurement.
- Wall times include the other Ollama pulls only for the first Gemma run; downloads had finished before the Qwen runs.

### Quality, scored by reading the outputs (fall case, 3 runs each)

| Model | Says the worker stepped off a ledge / fell into a floor opening | Step-off timing vs t0 | Hazard category chosen (correct is `fall_from_height`) | Invented content |
|---|---|---|---|---|
| qwen3-vl:8b-instruct | 2/3 ("fell from a raised platform", "steps off the platform edge"). Run 1: stage 1 looped, report is sensor-only and says no visual evidence. | Right: step-off at -0.8 s, falling from -0.3 s, in both good runs | `falling_object` 3/3 | "No fall protection / no supervisor oversight / policy not enforced" (not visible, written as fact in causes); "worker not wearing fall protection equipment"; one run lists the same "still falling" line 6 times |
| gemma3:12b | 0/3. "Walking forward ... stumbles ... falls forward ... on the ground". No ledge, no opening. | Wrong: fall placed at +0.3 s, not the step-off at -0.8 s | `slip_trip_fall` 3/3 | "Tripped", "holding something", "hard hat's sensors", "arms outstretched" |
| qwen2.5vl:7b | 0/3. Sees a person walking down a staircase and away; run 3 says "descending a staircase ... appearing to fall ... lying on the ground". | Wrong; run 3 sequence offsets are -20, -19, ... +6 s (integer clip seconds, not t0-relative, and a line for every second) | `falling_object` 3/3 | Staircase, tool in hand, "lying on the ground" for 20 s |

None of the three picks `fall_from_height`. All three quote the sensor numbers correctly (450 ms, 0.99 m, 12.4 g) and none breaks the location, blame or regulation rules in the prompt in the runs read.

### Struck-by case (test pattern, no real scene)

| Model | Behaviour |
|---|---|
| qwen3-vl:8b-instruct | Best. Correctly says "test pattern with no real-world environment", `contact_visible` = unclear 3/3, and states the video cannot confirm the near miss. Hazard category `falling_object` 2/3 (should be `struck_by`). |
| gemma3:12b | Says "the view is obstructed by a grey object" (the grey square of the pattern), `contact_visible` = no. The report then calls the object "falling" and "rectangular" (invented), category `falling_object` 3/3. |
| qwen2.5vl:7b | Worst: hallucinates a person ("worker is moving the arm with a tool", "striking a piece of wood") in 3/3 runs and builds the report on it, though the category `struck_by` is right. |

Hallucination summary: Qwen2.5-VL invents people and tools on an empty test pattern every time; Gemma invents a falling or rectangular object and a stumble; Qwen3-VL's observations are the most faithful, but its causes section invents site conditions (fall protection, supervision, policy) that nothing in the inputs supports.

## 3. Cloud baseline

**Theoretical unless the row says "measured". Nothing in this section about Claude was measured.**

### Measured by the coordinator (2026-11-08, user's key)

Same 30 s demo fall clip (640x480, 1.2 MB), prompt "Describe what happens in this clip in two sentences". This is a short-answer probe, not a full report.

| Item | Result |
|---|---|
| Files API upload / processing to ACTIVE | 2.0 s / 2.1 s; the 30 s video cost 1,890 prompt tokens |
| gemini-3.5-flash | 2.7 s. "A man looking down at his phone walks near the unguarded edge of an upper floor. Distracted, he steps off the ledge and falls into the opening below." Correct. |
| gemini-3.5-flash-lite | 1.6 s. Detail errors ("steps backward", "stairwell") |
| gemini-3.8-flash | 117 s plain text, 89 s with JSON output; accurate but slow server-side; 503 "high demand" in earlier runs |
| gemini-3.7-flash | 503 high demand |
| gemini-2.5-flash | 404, not available to new users |
| Claude | Not measured: the user's Anthropic account has no credit ("credit balance is too low") |

gemini-3.5-flash is the realistic cloud option. Against the clip's ground truth it names the ledge, the opening and the step-off, which is what the local models mostly miss. It also reads the sound track and the whole 30 s at native frame rate.

### Theoretical estimates

| | Gemini Flash-class video (stage 1) | Claude Opus 5.5 (stage 2 writer) | Claude Sonnet 5.5 (stage 2 writer) |
|---|---|---|---|
| List price per MTok in / out | $1.50 / $9.00 (worker table `gemini-default` in `worker/src/config.ts`; same figure in public listings, see sources) | $4 / $20 (worker table and claude-api skill; cache reads $0.20) | $2 / $10 (claude-api skill; cache reads $0.20) |
| Context / max output | n/a (video about 63 tokens per second of clip, from the measured 1,890 tokens per 30 s) | 1M / 128K | 1M / 128K |
| Typical tokens per event (estimate) | about 2.4k in (video plus prompts), about 0.8k out | about 3k in, about 4k out (report about 2k plus adaptive thinking; thinking bills as output) | same |
| Cost per event (estimate) | about $0.011 | about $0.092 | about $0.046 |
| Expected latency | the spec estimates 1.5 to 3 min for the whole hybrid event; the measured 3.5-flash probe took 2.7 s plus 4 s upload, but other Gemini models were far slower or returned 503 | tens of seconds; effort `medium` is the Opus 5.5 default | similar or a little quicker |

Per event, hybrid with Opus 5.5 is therefore about $0.10, hybrid with Sonnet 5.5 about $0.06, Gemini alone about $0.02 (all estimates; the worker's spend cap of $25 equals about 250 Opus-hybrid events). Local is $0 per event plus electricity.

Other cloud facts:
- Quality: Gemini sees the whole video natively (30 s at its own frame rate, with audio); the local models see 16 sampled frames and no audio. The step-off at -0.8 s and the fall are a 1.5 s feature in a 30 s clip, which is exactly what sparse sampling can miss.
- Reliability: the user has hit Gemini 503 "high demand" errors, confirmed again in the measured probes above (3.7-flash 503, 3.8-flash slow). The worker already retries transient errors and falls back to a sensor-only report. Claude's account is currently out of credit. Cloud needs working internet on site.
- Privacy: the video, worker names and sensor data leave the laptop for Google and Anthropic. Local keeps everything on the machine.
- Sources: worker `config.ts` price table; claude-api skill model table (cached 2026-10-06) for Claude prices, context and Opus 5.5 effort default; web search for Gemini 3.5 Flash text pricing (pricing aggregators such as https://www.thepricer.org/how-much-does-gemini-3-5-flash-cost/ and https://pricepertoken.com/pricing-page/model/google-gemini-3.5-flash: $1.50 in / $9.00 out). Secondary sources: confirm on Google's pricing page (ai.google.dev/gemini-api/docs/pricing) before committing. No per-second video rate was found.

## 4. Key differences

| | Local (Ollama) | Cloud (Gemini + Claude) |
|---|---|---|
| Video input | 16 sampled frames, no audio | Whole clip, native, with audio |
| Fall from ledge understood | Best local model: 2 of 3 runs | Measured correct on the probe (3.5 Flash) |
| Structured output | Schema enforced by grammar: always valid JSON, but a vision-stage loop can burn 6k tokens (about 6 min) | Schema enforced by the provider |
| Cost per event | $0 | about $0.02 to $0.10 (estimate) |
| Latency | 80 to 130 s warm; first call after idle adds a load of 3 to 5 s | Seconds to minutes, wide variance by provider load |
| Availability | Works offline; no 503s | Depends on internet, Gemini load, Anthropic credit |
| Privacy | Stays on the laptop | Leaves the laptop |
| Laptop load | 11 to 14 GB RAM and full GPU for the length of the run | None |

## 5. Pros and cons

**Local (Qwen3-VL 8B via Ollama)**
- Pros: free per event; offline; private; no rate limits or 503s; schema-valid JSON; same prompts and schemas work unchanged.
- Cons: weaker video understanding (frame sampling, no audio); wrong hazard category in every run; invents causes; one vision-stage loop in six runs; 80 to 130 s of heavy GPU and 11 to 14 GB RAM during the pitch demo; 6 to 8 GB of model downloads per laptop; model tags change (the plain `qwen3-vl:8b` tag is a thinking model and failed outright).

**Cloud (Gemini 3.5 Flash + Claude)**
- Pros: native whole-video understanding; much stronger writer; no load on the laptop; small per-event cost.
- Cons: 503s and slow models (measured today); Anthropic credit is empty right now; data leaves the laptop; needs internet; cost scales with events.

## 6. Decision matrix

Scores 1 (worst) to 5 (best). Weights sum to 100. Weighted score = sum(weight x score) / 100.

| Criterion | Weight | Local only (Qwen3-VL 8B) | Cloud hybrid (Gemini + Claude) | Cloud Gemini-only | Cloud default, local fallback |
|---|---|---|---|---|---|
| Report quality | 20 | 2 | 5 | 4 | 5 |
| Video understanding | 15 | 2 | 5 | 5 | 5 |
| Cost per event | 10 | 5 | 3 | 4 | 3 |
| Latency | 10 | 3 | 3 | 4 | 3 |
| Reliability / offline | 15 | 4 | 2 | 2 | 4 |
| Privacy | 10 | 5 | 2 | 2 | 2 |
| Setup / maintenance | 5 | 2 | 4 | 5 | 3 |
| Laptop load during the pitch | 15 | 1 | 5 | 5 | 4 |
| **Weighted total (out of 5)** | 100 | **2.85** | **3.80** | **3.85** | **3.90** |

The weights favour a demo: quality, reliability and not overloading the laptop matter most; cost per event barely matters at this volume. The scores are judgements from the numbers above, not measurements. Local-only loses on quality, video understanding and laptop load; it wins on cost and privacy. If privacy or offline operation were a hard requirement (a customer whose site policy bans cloud video), the ranking would flip toward local, and the quality gap would then need a bigger local model or better prompts.

## 7. Recommendation

1. **Default to the cloud pipeline already in the worker** (Gemini 3.5 Flash observes the clip, Claude writes the report). It is the only path that read the fall correctly in the measured probes, and it adds no load to the demo laptop.
2. **Keep Ollama as a fallback, not a primary.** When Gemini returns 503 after its retries, or there is no internet, or the Anthropic key has no credit, run the local Qwen3-VL 8B pipeline instead of the current sensor-only report. A local video-informed draft with a visible "locally generated, lower confidence" note beats a sensor-only draft. Load the model on demand and unload it afterwards (`keep_alive: 0`) so it does not sit on 9 GB during the demo.
3. **Before relying on the fallback:** (a) cap the vision stage (`num_predict` about 1,500 and a `maxItems` on observations, or retry once) because of the looping failure; (b) add a post-check that rejects `falling_object` when the sensor type is `fall` or ground the category from the device event type; (c) add a line to the writer prompt forbidding invented site conditions (fall protection, supervision) not in the inputs; (d) fix the Anthropic account credit.
4. **Pitch day:** consider pre-generating the reports for the demo events with the cloud pipeline, and keep local as the live-offline story (tell the audience it is a lower-confidence mode). Do not run Ollama and the full demo stack on the same 24 GB laptop unless the local run is deliberate.
5. A cheaper cloud writer is an option: Sonnet 5.5 at about $0.046 per event vs about $0.092 for Opus 5.5 (estimates), if quality holds on a side-by-side. Not tested here.

## 8. Not measured

- **Claude latency and report quality**: the account has no credit; only theoretical numbers above. No full Gemini report-quality run either (only the two-sentence probe); I made no cloud calls myself.
- **A text-only writer split** (a stronger text model for stage 2 behind a small vision model): not tested. Stage 2 is the faster stage (45 to 90 s), so a two-model split mostly adds model-swap time on 24 GB.
- **Larger local models** (Gemma 3 27B, Qwen3-VL 30B-A3B / 32B) that may fit with a smaller context: not tested, three-model limit.
- **More or denser frames, bigger resolution, or a smaller number of frames** to cut Qwen3-VL's roughly 1,100 tokens per image: not swept.
- **Audio**: no local model got audio.
- **Peak system memory** is the Ollama process RSS plus `/api/ps`; whole-machine pressure (`vm_stat`) and swapping were not recorded.
- **True cold-boot load** (empty page cache) was not forced.
- Hallucination and fall-correctness scores are my reading of the outputs, backed by keyword flags in `analyze.py`; they are not an automated metric and 3 runs per cell is a small sample.
- The clip is third-person footage, not a hard-hat camera view, because that is what the demo fixture is.

## 9. Full example reports (verbatim)

Best local model, `qwen3-vl:8b-instruct`, the LLM-written analysis fields exactly as returned by Ollama (stage 2 output, before the worker adds header, risk score, sign-off and footer). Files: `results/raw/qwen3-vl-8b-instruct__fall__run2.report.json` and `results/raw/qwen3-vl-8b-instruct__struck_by__run2.report.json`.

### Example: fall case (qwen3-vl:8b-instruct, run 2)

```json
{
  "summary": "Worker fell from a raised platform after stepping off the edge, with the hard hat recording a 12.4 g impact during the 450 ms free fall. The fall was fully visible in video and confirmed by sensor data.",
  "sequence_of_events": [
    {
      "offset_s": -6.0,
      "description": "Scene is an indoor construction area with a raised platform and lower level.",
      "source": "video"
    },
    {
      "offset_s": -0.9,
      "description": "Worker standing on platform, holding a long object, preparing to move.",
      "source": "video"
    },
    {
      "offset_s": -0.8,
      "description": "Worker begins stepping down from platform toward lower level.",
      "source": "video"
    },
    {
      "offset_s": -0.6,
      "description": "Worker mid-step, descending platform edge with one leg extended.",
      "source": "video"
    },
    {
      "offset_s": -0.4,
      "description": "Worker’s body leans forward while descending platform edge.",
      "source": "video"
    },
    {
      "offset_s": -0.3,
      "description": "Worker fully in process of falling, both legs dangling over edge.",
      "source": "video"
    },
    {
      "offset_s": -0.1,
      "description": "Worker falling downward, arms extended as if bracing or stabilizing.",
      "source": "video"
    },
    {
      "offset_s": 0.0,
      "description": "Worker in mid-fall, body oriented downward, arms still extended.",
      "source": "video"
    },
    {
      "offset_s": 0.1,
      "description": "Worker continues falling, mostly out of frame’s upper view.",
      "source": "video"
    },
    {
      "offset_s": 0.0,
      "description": "Fall detected: 450 ms free fall (estimated drop 0.99 m) ending in 12.4 g impact.",
      "source": "sensor"
    },
    {
      "offset_s": 0.1,
      "description": "Confirmation buzz on both motors (1.5 s) to notify wearer event was recorded.",
      "source": "sensor"
    }
  ],
  "evidence": {
    "clip_refs": [
      {
        "offset_s": -0.3,
        "observation": "Worker fully in process of falling, both legs dangling over edge."
      },
      {
        "offset_s": 0.0,
        "observation": "Worker in mid-fall, body oriented downward, arms still extended."
      },
      {
        "offset_s": 0.1,
        "observation": "Worker continues falling, mostly out of frame’s upper view."
      }
    ],
    "key_sensor_values": [
      {
        "name": "Peak acceleration",
        "value": "12.4 g"
      },
      {
        "name": "Free-fall duration",
        "value": "450 ms"
      },
      {
        "name": "Estimated drop height",
        "value": "0.99 m"
      },
      {
        "name": "Hat on at trigger",
        "value": "yes"
      }
    ],
    "environment": "Near-wearer conditions (indicative): CO2 612 ppm, temperature 21.4 C, relative humidity 38%."
  },
  "hazard": {
    "category": "falling_object",
    "energy_source": "gravitational potential energy from height of 0.99 m"
  },
  "risk": {
    "severity": 3,
    "likelihood": 4,
    "severity_justification": "The fall resulted in a 12.4 g impact, which could cause injury to the head or spine, but the drop height was low (0.99 m).",
    "likelihood_justification": "The worker was on a raised platform and stepped off without apparent warning or fall protection, indicating a foreseeable hazard."
  },
  "causes": {
    "immediate": [
      {
        "kind": "substandard_act",
        "description": "Worker stepped off the platform edge without using fall protection or securing their position."
      },
      {
        "kind": "substandard_condition",
        "description": "No fall protection systems (e.g., guardrails, harnesses) were visible or in use at the platform edge."
      }
    ],
    "root_five_whys": [
      "Why did the worker step off the platform? Because they were not using fall protection.",
      "Why was fall protection not used? Because it was not provided or required at the platform edge.",
      "Why was it not provided? Because the site’s fall protection policy was not enforced or communicated.",
      "Why was the policy not enforced? Because there was no visible signage or supervisor oversight.",
      "Why was there no supervisor oversight? Because the supervisor was not monitoring the area in real-time."
    ]
  },
  "corrective_actions": [
    {
      "description": "Install guardrails or temporary fall protection systems at all platform edges.",
      "control_level": "engineering",
      "suggested_owner_role": "Site supervisor"
    },
    {
      "description": "Require all workers to use fall protection when working at heights, even low ones.",
      "control_level": "administrative",
      "suggested_owner_role": "Health and safety coordinator"
    },
    {
      "description": "Provide training on fall hazards and PPE use for all crew members.",
      "control_level": "administrative",
      "suggested_owner_role": "Health and safety coordinator"
    },
    {
      "description": "Implement real-time monitoring of high-risk areas with supervisors or automated alerts.",
      "control_level": "administrative",
      "suggested_owner_role": "Site supervisor"
    }
  ],
  "regulatory_note": "This event may meet a provincial serious-incident or dangerous-occurrence notification threshold. Verify with your provincial OHS regulator.",
  "confidence_and_gaps": "Location not specified (unknown). No video gaps; all fall sequence visible. Environment values are near-wearer conditions (indicative).",
  "classification_review": {
    "suggested_class": null,
    "reason": null
  }
}
```

### Example: struck_by case (qwen3-vl:8b-instruct, run 2)

```json
{
  "summary": "The device detected a near miss at t0 with a closing object at 4.2 m/s and 0.8 s to contact, but no physical contact occurred. The video shows a test pattern with no real-world environment, so no actual hazard or impact was observed. The event was correctly classified as a near miss by the device.",
  "sequence_of_events": [
    {
      "offset_s": -0.9,
      "description": "Rear sensor R2 detected an approaching object with a closing rate of 4.2 m/s, time to contact of 0.8 s, and range of 3.40 m. A warning pulse was triggered on the back motor.",
      "source": "sensor"
    },
    {
      "offset_s": 0.0,
      "description": "Near miss confirmed on sensor R2: object closing at 4.2 m/s, 0.8 s to contact, 3.40 m away, with no impact detected.",
      "source": "sensor"
    },
    {
      "offset_s": -0.5,
      "description": "Range sensor R2 recorded a closest echo at 0.10 m, indicating the object passed within 0.10 m of the hat.",
      "source": "sensor"
    },
    {
      "offset_s": -15.0,
      "description": "Video shows a test pattern with no real-world environment.",
      "source": "video"
    },
    {
      "offset_s": -6.0,
      "description": "Video shows a test pattern with a gray square in the lower-left and a rainbow gradient line.",
      "source": "video"
    },
    {
      "offset_s": -0.9,
      "description": "Video shows a gray square in the lower-left and rainbow gradient line.",
      "source": "video"
    },
    {
      "offset_s": 0.0,
      "description": "Video shows the gray square and rainbow gradient line still present.",
      "source": "video"
    }
  ],
  "evidence": {
    "clip_refs": [
      {
        "offset_s": -0.9,
        "observation": "Sensor R2 detected an object closing at 4.2 m/s, 0.8 s to contact, 3.40 m away, with no impact detected."
      },
      {
        "offset_s": 0.0,
        "observation": "Near miss confirmed on sensor R2 with no impact detected."
      },
      {
        "offset_s": -0.5,
        "observation": "Range sensor R2 recorded a closest echo at 0.10 m."
      }
    ],
    "key_sensor_values": [
      {
        "name": "Closing rate",
        "value": "4.2 m/s"
      },
      {
        "name": "Time to contact",
        "value": "0.8 s"
      },
      {
        "name": "Range at trigger",
        "value": "3.40 m"
      },
      {
        "name": "Triggering sensor",
        "value": "R2"
      },
      {
        "name": "Hat on at trigger",
        "value": "yes"
      }
    ],
    "environment": "Near-wearer conditions (indicative): CO2 612 ppm, temperature 21.4 C, relative humidity 38%."
  },
  "hazard": {
    "category": "falling_object",
    "energy_source": "kinetic energy from an object closing at 4.2 m/s"
  },
  "risk": {
    "severity": 3,
    "likelihood": 4,
    "severity_justification": "Potential for minor injury (e.g., bruising or minor laceration) if impact occurred.",
    "likelihood_justification": "High likelihood due to object closing at 4.2 m/s and passing within 0.10 m of the hat, but no impact occurred."
  },
  "causes": {
    "immediate": [
      {
        "kind": "substandard_act",
        "description": "The object was not controlled or removed from the worker’s path before reaching 0.10 m."
      },
      {
        "kind": "substandard_condition",
        "description": "The environment was not monitored for approaching objects, and no visual or auditory warning was provided to the worker."
      }
    ],
    "root_five_whys": [
      "Why was the object not controlled? Because no one was monitoring the area for approaching objects.",
      "Why was no one monitoring the area? Because the worker was not provided with a visual or auditory warning system.",
      "Why was no warning system provided? Because the site did not implement a system to detect and warn of approaching objects.",
      "Why was no such system implemented? Because the site did not assess the risk of falling objects in the area.",
      "Why was no risk assessment conducted? Because the site did not have a formal hazard identification and risk assessment process."
    ]
  },
  "corrective_actions": [
    {
      "description": "Implement a real-time object detection and warning system for all workers in high-risk areas.",
      "control_level": "engineering",
      "suggested_owner_role": "Site supervisor"
    },
    {
      "description": "Conduct a site-wide hazard identification and risk assessment to identify all potential falling object hazards.",
      "control_level": "administrative",
      "suggested_owner_role": "Health and safety coordinator"
    },
    {
      "description": "Provide all workers with training on how to respond to near-miss events and how to avoid falling object hazards.",
      "control_level": "administrative",
      "suggested_owner_role": "Health and safety coordinator"
    },
    {
      "description": "Install physical barriers or protective equipment in areas where falling objects are a risk.",
      "control_level": "engineering",
      "suggested_owner_role": "Site supervisor"
    }
  ],
  "regulatory_note": "This event may meet a provincial serious-incident or dangerous-occurrence notification threshold. Please verify with your provincial OHS regulator.",
  "confidence_and_gaps": "No video was available to confirm physical contact or impact. The environment was not real-world, and no people, equipment, or materials were visible. The location is unknown.",
  "classification_review": {
    "suggested_class": null,
    "reason": null
  }
}
```

Caveat on the fall example: it picks `falling_object` (should be `fall_from_height`) and its root causes about fall protection and supervision are invented, not evidenced by the inputs.
