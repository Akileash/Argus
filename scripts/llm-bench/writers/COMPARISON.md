# Report writer comparison (measured, 2026-11-08)

Same two real clips, the real worker pipeline (`worker/scripts/compare-writers.ts`), in-memory store, one run each.
Raw reports: `results.json`.

- **fall**: a worker looking at a phone steps off an unguarded ledge into a floor opening (security-camera view).
- **scooter**: first-person e-scooter crash on a wooden pier, head impact.

| Writer | Time (fall / scooter) | Cost per report (fall / scooter) |
|---|---|---|
| A: Gemini 3.5 Flash alone (Google served 3.6 Flash) | 24 s / 25 s | $0.032 / $0.036 |
| B: Gemini observes, Claude Sonnet 5.5 writes | 42 s / 40 s | $0.061 / $0.059 |
| C: Gemini observes, Claude Opus 5.5 writes | 47 s / 55 s | $0.094 / $0.115 |

All six reports were valid, used "Location to be determined", and quoted the sensor values correctly.

## Quality

**A: Gemini alone.** Fluent and concise, and it picked the right hazard for the fall (fall from height).
It states unproven things as fact: the "stairwell void", "edge protection was omitted during active work",
"operating on a non-designated pedestrian walkway", "stood up without visible assistance". Its 5-whys
invents site-process failures. Three corrective actions each, with no medical follow-up and no
"take the hat out of service" after a ≥ 16 g impact.

**B: Claude Sonnet 5.5.** Careful investigation writing. Keeps what is known apart from what isn't
("whether any was available or required is not known"). Five actions per report, covering every level of
the hierarchy of controls, including medical assessment and taking the hard hat out of service after the
impact. Noted the clip is a fixed third-person camera, not the hat. One error came from the video step,
not the writer: Gemini's description said the worker stepped *backward*, and Sonnet repeated it.

**C: Claude Opus 5.5.** The best judgement. Its 5-whys says "to be determined" instead of inventing
causes. It flagged that the fall clip is third-person and must be confirmed as linked to the hat, and
that the scooter scene "looks unusual for construction work, so the supervisor should confirm … whether
the travel was work-related". Six actions on the fall, including medical assessment and removing the
impacted hat. Slowest and most expensive.

## Recommendation

Use **Claude Sonnet 5.5** to write reports (`REPORT_MODE=hybrid`, `ANTHROPIC_MODEL=claude-sonnet-5-5`).
It is clearly better than Gemini where it matters for a safety manager (no invented causes, sensible
actions) for about $0.03 more per report, at half of Opus's cost. Opus is better again on judgement;
switch to it if Sonnet's reports fall short on real hat footage.
Gemini alone remains the cheapest and fastest option.
