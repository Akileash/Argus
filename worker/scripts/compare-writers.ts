// Side-by-side: who writes the report. Same real clips, real providers, in-memory store (no database).
import { readFileSync, writeFileSync } from "node:fs";
import { loadConfig } from "../src/config";
import { runOnce } from "../src/pipeline/run";
import { createGeminiClient } from "../src/providers/gemini";
import { createClaudeClient } from "../src/providers/claude";
import { seed, silent } from "../test/helpers";

const OUT = "../scripts/llm-bench/writers"; // run from worker/: pnpm exec tsx scripts/compare-writers.ts (paths are cwd-relative)
const cases = [
  { id: "fall", type: "fall" as const, clip: `${OUT}/.fall-clip.mp4`, what: "Worker looking at a phone steps off an unguarded ledge and falls into a floor opening (security-camera view)." },
  { id: "scooter", type: "head_impact" as const, clip: `${OUT}/.scooter-clip.mp4`, what: "First-person e-scooter rider crashes on a wooden pier; hand and head hit the deck." },
];
const writers = [
  { id: "A-gemini", label: "Gemini 3.5 Flash writes (one call with the video)", mode: "gemini" as const, anthropicModel: "" },
  { id: "B-sonnet", label: "Gemini observes, Claude Sonnet 5.5 writes", mode: "hybrid" as const, anthropicModel: "claude-sonnet-5-5" },
  { id: "C-opus", label: "Gemini observes, Claude Opus 5.5 writes", mode: "hybrid" as const, anthropicModel: "claude-opus-5-5" },
];

const base = loadConfig(process.env);
const gemini = createGeminiClient(process.env.GEMINI_API_KEY!);
const claude = createClaudeClient(process.env.ANTHROPIC_API_KEY!);
const results: unknown[] = [];

await Promise.all(
  cases.flatMap((c) =>
    writers.map(async (w) => {
      const eventId = crypto.randomUUID();
      const store = seed(c.type, undefined, eventId);
      store.files.set(`org/${eventId}/clip.mp4`, new Uint8Array(readFileSync(c.clip)));
      const cfg = { ...base, mode: w.mode, anthropicModel: w.anthropicModel || base.anthropicModel, retryDelayMs: 2000, spendCapUsd: 5 };
      const t = Date.now();
      await runOnce({ store, cfg, providers: { gemini, claude: w.mode === "hybrid" ? claude : undefined }, log: silent });
      const ms = Date.now() - t;
      const ev = store.events.get(eventId)!;
      const rep = store.reports.get(eventId);
      const rows = store.analyses.map((a) => ({ stage: a.stage, model: a.model, in: a.input_tokens, out: a.output_tokens, cost: a.cost_usd, ms: a.latency_ms }));
      const cost = rows.reduce((s, r) => s + r.cost, 0);
      results.push({ case: c.id, what: c.what, writer: w.id, label: w.label, status: ev.status, error: ev.error ?? null, total_ms: ms, cost_usd: cost, analyses: rows, report: rep?.report_json ?? null });
      console.log(`${c.id.padEnd(8)} ${w.id.padEnd(9)} ${String(ev.status).padEnd(6)} ${(ms / 1000).toFixed(1)}s $${cost.toFixed(4)} ${ev.error ? "ERR " + String(ev.error).slice(0, 90) : ""}`);
    }),
  ),
);
writeFileSync(`${OUT}/results.json`, JSON.stringify(results, null, 2));
