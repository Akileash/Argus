import { describe, expect, it } from "vitest";
import { processClaimed } from "../src/pipeline/run";
import type { GeminiClient } from "../src/providers/types";
import { cfg, evilLlmOutput, mockClaude, mockGemini, OBS, seed, silent } from "./helpers";

const hangingGemini: GeminiClient = { generateWithVideo: () => new Promise<never>(() => {}) };
const ID = "6f1d2c1a-3b4e-4f5a-8b6c-7d8e9f0a1b2c";

describe("per-event wall-clock budget (WORKER_EVENT_BUDGET_S, used on Vercel)", () => {
  it("a hanging Gemini call is cut at the budget, not at the long LLM_CALL_TIMEOUT_S: sensor-only report, event ready", async () => {
    const store = seed("struck_by_near_miss");
    await store.claimNext(3);
    const t = Date.now();
    await processClaimed(
      { store, cfg: cfg({ mode: "gemini", llmCallTimeoutS: 180, eventBudgetMs: 2500, maxAttempts: 1 }), providers: { gemini: hangingGemini }, log: silent },
      ID,
    );
    expect(Date.now() - t).toBeLessThan(5000);
    expect(store.events.get(ID)!.status).toBe("ready");
    expect(store.analyses.some((a) => a.model === "sensor-only")).toBe(true);
  }, 15_000);

  it("a budget already used up skips paid calls entirely (no provider call), still produces a report", async () => {
    const store = seed("struck_by_near_miss");
    await store.claimNext(3);
    const gemini = mockGemini(() => ({}));
    // 1 ms budget: nothing paid can start (MIN_CALL_MS), the report is written from sensor data
    await processClaimed({ store, cfg: cfg({ mode: "gemini", eventBudgetMs: 1 }), providers: { gemini }, log: silent }, ID);
    expect(gemini.calls).toHaveLength(0);
    expect(store.events.get(ID)!.status).toBe("ready");
  });

  it("hybrid: Gemini stage keeps one Claude call of time in reserve", async () => {
    const store = seed("struck_by_near_miss");
    await store.claimNext(3);
    const gemini = mockGemini(() => OBS);
    const claude = mockClaude(() => evilLlmOutput());
    // budget (50 s) < reserve (llmCallTimeoutS 60 s): the Gemini stage cannot start
    await processClaimed(
      { store, cfg: cfg({ mode: "hybrid", llmCallTimeoutS: 60, eventBudgetMs: 50_000, anthropicModel: "claude-test" }), providers: { gemini, claude }, log: silent },
      ID,
    );
    expect(gemini.calls).toHaveLength(0);
    expect(claude.calls).toHaveLength(1); // the reserved time went to Claude writing the sensor-only report
    expect(store.events.get(ID)!.status).toBe("ready");
  });

  it("is off by default (0): the long-running worker is unchanged", async () => {
    const store = seed("struck_by_near_miss");
    await store.claimNext(3);
    const gemini = mockGemini(() => ({}));
    expect(cfg().eventBudgetMs).toBe(0);
    await processClaimed({ store, cfg: cfg({ mode: "gemini" }), providers: { gemini }, log: silent }, ID);
    expect(gemini.calls.length).toBeGreaterThan(0);
  });
});
