import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadConfig } from "../src/config";
import { processClaimed } from "../src/pipeline/run";
import { createGeminiClient, type GenAiLike } from "../src/providers/gemini";
import { isTransientError } from "../src/providers/transient";
import { MemoryStore } from "../src/store/memory";
import { cfg, evilLlmOutput, jsonSchemaValid, mockClaude, OBS, seed, silent } from "./helpers";

const id = "6f1d2c1a-3b4e-4f5a-8b6c-7d8e9f0a1b2c";
const HIGH_DEMAND = '{"error":{"code":503,"message":"This model is currently experiencing high demand. Spikes in demand are usually temporary. Please try again later.","status":"UNAVAILABLE"}}';

/** Shaped like @google/genai's ApiError: message = response body text, status = HTTP code. */
function apiError(status: number, message = HIGH_DEMAND) {
  return Object.assign(new Error(message), { name: "ApiError", status });
}

type Step = "ok" | "hang" | Error;
/** Fake Gemini SDK: generateContent follows `steps` (the last one repeats). */
function fakeAi(steps: Step[], obsJson: unknown = evilLlmOutput()) {
  const log = { uploads: 0, generates: 0, deletes: 0 };
  const ai = {
    files: {
      upload: async () => (log.uploads++, { name: "files/abc", state: "ACTIVE", uri: "https://example.invalid/f", mimeType: "video/mp4" }),
      get: async () => ({}),
      delete: async () => (log.deletes++, {}),
    },
    models: {
      generateContent: async () => {
        const s = steps[Math.min(log.generates, steps.length - 1)]!;
        log.generates++;
        if (s === "hang") return new Promise<never>(() => {});
        if (s instanceof Error) throw s;
        return { text: JSON.stringify(obsJson), usageMetadata: { promptTokenCount: 1000, candidatesTokenCount: 500 }, modelVersion: "gemini-test" };
      },
    },
  };
  return { ai: ai as unknown as GenAiLike, log };
}

const deps = (store: MemoryStore, c: ReturnType<typeof cfg>, providers: object) => ({ store, cfg: c, providers, log: silent });

/** Run processClaimed to completion under fake timers. */
async function run(store: MemoryStore, c: ReturnType<typeof cfg>, providers: object, advanceMs = 600_000) {
  const done = processClaimed(deps(store, c, providers), id);
  await vi.advanceTimersByTimeAsync(advanceMs);
  await done;
}

function claudeSensorOutput() {
  return evilLlmOutput({
    confidence_and_gaps: "Based on sensor data.",
    sequence_of_events: [{ offset_s: 0, description: "Impact registered", source: "video" }],
  });
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("isTransientError reads the SDK error shape", () => {
  it("recognises status codes, Google status strings and network errors", () => {
    for (const s of [429, 500, 502, 503, 504]) expect(isTransientError(apiError(s, "x"))).toBe(true);
    expect(isTransientError(apiError(400, "bad request"))).toBe(false);
    expect(isTransientError(apiError(401, "UNAVAILABLE-looking text but 401"))).toBe(false);
    expect(isTransientError(new Error('{"error":{"code":429,"status":"RESOURCE_EXHAUSTED","message":"quota"}}'))).toBe(true);
    expect(isTransientError(new Error("This model is currently experiencing high demand"))).toBe(true);
    expect(isTransientError(Object.assign(new TypeError("fetch failed"), { cause: { code: "ECONNRESET" } }))).toBe(true);
    expect(isTransientError(new Error("Gemini returned no text"))).toBe(false);
  });
  it("GEMINI_TRANSIENT_RETRIES defaults to 3 and is configurable", () => {
    expect(loadConfig({}).geminiTransientRetries).toBe(3);
    expect(loadConfig({ GEMINI_TRANSIENT_RETRIES: "5" }).geminiTransientRetries).toBe(5);
    expect(loadConfig({ GEMINI_TRANSIENT_RETRIES: "0" }).geminiTransientRetries).toBe(3);
  });
});

describe("transient Gemini errors", () => {
  it("503 then success: one report, video used, no sensor-only row", async () => {
    const { ai, log } = fakeAi([apiError(503), "ok"]);
    const store = seed("struck_by_near_miss");
    await run(store, cfg({ mode: "gemini" }), { gemini: createGeminiClient("k", { ai }) });
    const ev = store.events.get(id)!;
    expect(ev.status, ev.error ?? "").toBe("ready");
    expect(log.generates).toBe(2);
    expect(log.uploads).toBe(1);
    expect(store.reports.size).toBe(1);
    expect(store.analyses.map((a) => a.model)).not.toContain("sensor-only");
    expect(store.analyses.find((a) => a.stage === "report_draft")!.model).toBe("gemini-test");
  });

  it("backoff waits roughly 30-45 s in all before giving up, and refreshes the heartbeat", async () => {
    const { ai, log } = fakeAi([apiError(503)]);
    const store = seed("struck_by_near_miss");
    const t0 = Date.now();
    const done = processClaimed(deps(store, cfg({ mode: "gemini", heartbeatMs: 1_000_000 }), { gemini: createGeminiClient("k", { ai }) }), id);
    await vi.advanceTimersByTimeAsync(29_000);
    expect(log.generates).toBeLessThan(3); // still backing off
    await vi.advanceTimersByTimeAsync(20_000);
    await done;
    expect(log.generates).toBe(3);
    expect(Date.now() - t0).toBeLessThan(50_000);
    expect(store.events.get(id)!.status).toBe("ready");
  });

  it("hybrid, 503 x3: Claude writes the report from sensor data and says why; video_skipped is recorded", async () => {
    const { ai, log } = fakeAi([apiError(503)]);
    const claude = mockClaude(() => claudeSensorOutput());
    const store = seed("struck_by_near_miss");
    await run(store, cfg({ mode: "hybrid", anthropicModel: "claude-test" }), { gemini: createGeminiClient("k", { ai }), claude });
    const ev = store.events.get(id)!;
    expect(ev.status, ev.error ?? "").toBe("ready");
    expect(log.generates).toBe(3);
    expect(log.deletes).toBe(1);
    expect(claude.calls).toHaveLength(1);
    expect(claude.calls[0]!.prompt).toMatch(/no video analysis is available/);
    expect(store.reports.size).toBe(1);
    const r = store.reports.get(id)!.report_json;
    expect(jsonSchemaValid(r).ok).toBe(true);
    expect(r.confidence_and_gaps).toMatch(/could not be analysed/);
    expect(r.confidence_and_gaps).toMatch(/Gemini was unavailable \(high demand\)/);
    expect(r.evidence.clip_refs).toEqual([]);
    expect(r.sequence_of_events.every((x) => x.source === "sensor")).toBe(true);
    expect(store.analyses.find((a) => a.stage === "report_draft")!.model).toBe("claude-test");
    // The dashboard's noVideo flag keys on an analyses row with model "sensor-only".
    const skipped = store.analyses.find((a) => a.stage === "video_skipped")!;
    expect(skipped).toMatchObject({ model: "sensor-only", cost_usd: 0 });
    expect(JSON.stringify(skipped.output_json)).toMatch(/high demand/);
  });

  it("hybrid, Gemini generate hangs: timeout -> Claude sensor-based report, not a failure, no video retry", async () => {
    const { ai, log } = fakeAi(["hang"]);
    const claude = mockClaude(() => claudeSensorOutput());
    const store = seed("struck_by_near_miss");
    await run(store, cfg({ mode: "hybrid", anthropicModel: "claude-test", llmCallTimeoutS: 180 }), { gemini: createGeminiClient("k", { ai }), claude });
    const ev = store.events.get(id)!;
    expect(ev.status, ev.error ?? "").toBe("ready");
    expect(log.generates).toBe(1); // a timeout is not retried
    expect(log.deletes).toBe(1);
    expect(claude.calls).toHaveLength(1);
    expect(store.reports.get(id)!.report_json.confidence_and_gaps).toMatch(/timed out after 180 s/);
    expect(store.analyses.find((a) => a.stage === "video_skipped")).toMatchObject({ model: "sensor-only" });
  });

  it("gemini mode, 503 x3: sensor-only draft with the reason", async () => {
    const { ai } = fakeAi([apiError(503)]);
    const store = seed("struck_by_near_miss");
    await run(store, cfg({ mode: "gemini" }), { gemini: createGeminiClient("k", { ai }) });
    const ev = store.events.get(id)!;
    expect(ev.status, ev.error ?? "").toBe("ready");
    const r = store.reports.get(id)!.report_json;
    expect(r.confidence_and_gaps).toMatch(/sensor data only/);
    expect(r.confidence_and_gaps).toMatch(/high demand/);
    expect(store.analyses.find((a) => a.stage === "report_draft")).toMatchObject({ model: "sensor-only", cost_usd: 0 });
    expect(store.analyses.find((a) => a.stage === "video_skipped")).toMatchObject({ model: "sensor-only" });
  });

  it("gemini mode, generate hangs: sensor-only report instead of a failed event", async () => {
    const { ai } = fakeAi(["hang"]);
    const store = seed("struck_by_near_miss");
    await run(store, cfg({ mode: "gemini", llmCallTimeoutS: 180 }), { gemini: createGeminiClient("k", { ai }) });
    expect(store.events.get(id)!.status).toBe("ready");
    expect(store.reports.get(id)!.report_json.confidence_and_gaps).toMatch(/timed out after 180 s/);
  });

  it("hybrid, Gemini down and Claude failing too: sensor-only draft, event ready", async () => {
    const { ai } = fakeAi([apiError(503)]);
    const claude = mockClaude(() => {
      throw new Error("Claude is down");
    });
    const store = seed("struck_by_near_miss");
    await run(store, cfg({ mode: "hybrid", anthropicModel: "claude-test" }), { gemini: createGeminiClient("k", { ai }), claude });
    const ev = store.events.get(id)!;
    expect(ev.status, ev.error ?? "").toBe("ready");
    const r = store.reports.get(id)!.report_json;
    expect(jsonSchemaValid(r).ok).toBe(true);
    expect(r.confidence_and_gaps).toMatch(/high demand/);
    expect(r.confidence_and_gaps).toMatch(/Claude was also unavailable/);
    expect(store.analyses.find((a) => a.stage === "report_draft")).toMatchObject({ model: "sensor-only" });
  });

  it("hybrid, Gemini fine but Claude hangs: sensor-only report, not a failure", async () => {
    const { ai } = fakeAi(["ok"], OBS);
    const claude = { generateJson: () => new Promise<never>(() => {}) };
    const store = seed("struck_by_near_miss");
    await run(store, cfg({ mode: "hybrid", anthropicModel: "claude-test" }), { gemini: createGeminiClient("k", { ai }), claude });
    expect(store.events.get(id)!.status).toBe("ready");
    expect(store.reports.get(id)!.report_json.confidence_and_gaps).toMatch(/timed out after 180 s/);
  });

  it("a non-transient Gemini error (400) is still an attempt failure, retried once, then failed", async () => {
    const { ai, log } = fakeAi([apiError(400, '{"error":{"code":400,"message":"bad","status":"INVALID_ARGUMENT"}}')]);
    const store = seed("struck_by_near_miss");
    await run(store, cfg({ mode: "gemini" }), { gemini: createGeminiClient("k", { ai }) }, 60_000);
    expect(log.generates).toBe(2); // 1 per attempt, no transient retry
    expect(store.events.get(id)!.status).toBe("failed");
    expect(store.reports.size).toBe(0);
  });

  it("spend cap still blocks paid calls, including the Claude sensor-based fallback", async () => {
    const { ai } = fakeAi([apiError(503)]);
    const claude = mockClaude(() => claudeSensorOutput());
    const store = seed("struck_by_near_miss");
    await store.insertAnalysis({ event_id: id, stage: "x", model: "m", input_tokens: 0, output_tokens: 0, cost_usd: 5, latency_ms: 0, output_json: {} });
    await run(store, cfg({ mode: "hybrid", anthropicModel: "claude-test", spendCapUsd: 5 }), { gemini: createGeminiClient("k", { ai }), claude });
    expect(claude.calls).toHaveLength(0);
    expect(store.events.get(id)!.status).toBe("failed");
    expect(store.events.get(id)!.error).toMatch(/LLM_SPEND_CAP_USD/);
  });

  it("every outcome ends ready-with-report or failed-with-error, never neither", async () => {
    const scenarios: Array<[string, object, "gemini" | "hybrid"]> = [];
    for (const mode of ["gemini", "hybrid"] as const) {
      for (const step of [apiError(503), apiError(429), "hang", new Error("boom")] as Step[]) {
        scenarios.push([`${mode}`, { ai: fakeAi([step]).ai }, mode]);
      }
    }
    for (const [, g, mode] of scenarios) {
      const store = seed("struck_by_near_miss");
      const claude = mockClaude(() => claudeSensorOutput());
      await run(store, cfg({ mode, anthropicModel: "claude-test" }), { gemini: createGeminiClient("k", { ai: (g as { ai: GenAiLike }).ai }), claude });
      const ev = store.events.get(id)!;
      if (ev.status === "ready") expect(store.reports.size).toBe(1);
      else {
        expect(ev.status).toBe("failed");
        expect(ev.error).toBeTruthy();
      }
    }
  });
});
