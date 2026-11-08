import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defaultStaleMs, loadConfig } from "../src/config";
import { VideoUnusableError } from "../src/errors";
import { processClaimed, runOnce } from "../src/pipeline/run";
import { createGeminiClient, type GenAiLike } from "../src/providers/gemini";
import { MemoryStore } from "../src/store/memory";
import { cfg, evilLlmOutput, fakeClip, jsonSchemaValid, mockClaude, mockGemini, noSleep, seed, silent } from "./helpers";

const id = "6f1d2c1a-3b4e-4f5a-8b6c-7d8e9f0a1b2c";
const deps = (store: MemoryStore, c = cfg({ mode: "gemini" }), providers = {}) => ({ store, cfg: c, providers, log: silent, sleep: noSleep });

function expectSensorOnlyReport(store: MemoryStore, why: RegExp) {
  const ev = store.events.get(id)!;
  expect(ev.status, ev.error ?? "").toBe("ready");
  expect(store.reports.size).toBe(1);
  const r = store.reports.get(id)!.report_json;
  expect(jsonSchemaValid(r).ok).toBe(true);
  expect(r.confidence_and_gaps).toMatch(/sensor data only/);
  expect(r.confidence_and_gaps).toMatch(why);
  expect(r.evidence.clip_refs).toEqual([]);
  const draftRow = store.analyses.find((a) => a.stage === "report_draft")!;
  expect(draftRow).toMatchObject({ model: "sensor-only", cost_usd: 0 });
  const skipped = store.analyses.find((a) => a.stage === "video_skipped")!;
  expect(skipped).toMatchObject({ model: "sensor-only" });
  expect(JSON.stringify(skipped.output_json)).toMatch(why);
}

/** A fake Gemini SDK: upload returns states[0], each files.get returns the next state (the last one repeats). */
function fakeAi(states: string[], opts: { hangGenerate?: boolean } = {}) {
  let i = 0;
  const log = { uploads: 0, gets: 0, deletes: [] as string[], generates: 0 };
  const file = (state: string) => ({ name: "files/abc", state, uri: "https://example.invalid/files/abc", mimeType: "video/mp4" });
  const ai = {
    files: {
      upload: async () => {
        log.uploads++;
        return file(states[0]!);
      },
      get: async () => {
        log.gets++;
        i = Math.min(i + 1, states.length - 1);
        return file(states[i]!);
      },
      delete: async ({ name }: { name: string }) => {
        log.deletes.push(name);
        return {};
      },
    },
    models: {
      generateContent: async () => {
        log.generates++;
        if (opts.hangGenerate) return new Promise<never>(() => {});
        return { text: JSON.stringify(evilLlmOutput()), usageMetadata: { promptTokenCount: 1000, candidatesTokenCount: 500 }, modelVersion: "gemini-test" };
      },
    },
  };
  return { ai: ai as unknown as GenAiLike, log };
}

describe("only real video goes to Gemini", () => {
  it("missing clip row -> sensor-only report, no provider call", async () => {
    const store = seed("struck_by_near_miss");
    store.events.get(id)!.ctx.clip = null;
    const gemini = mockGemini(() => evilLlmOutput());
    await processClaimed(deps(store, cfg({ mode: "gemini" }), { gemini }), id);
    expect(gemini.calls).toHaveLength(0);
    expectSensorOnlyReport(store, /clip missing/);
    expect(store.reports.get(id)!.report_json.confidence_and_gaps).toMatch(/^No usable video was received for this event \(/);
  });

  it("unconfirmed clip row -> sensor-only, never downloaded", async () => {
    const store = seed("struck_by_near_miss");
    store.events.get(id)!.ctx.clip!.confirmed = false;
    const gemini = mockGemini(() => evilLlmOutput());
    const downloaded: string[] = [];
    const orig = store.download.bind(store);
    store.download = async (p) => (downloaded.push(p), orig(p));
    await processClaimed(deps(store, cfg({ mode: "gemini" }), { gemini }), id);
    expect(gemini.calls).toHaveLength(0);
    expect(downloaded.filter((p) => p.endsWith("clip.mp4"))).toEqual([]);
    expectSensorOnlyReport(store, /never confirmed/);
  });

  it("clip row declares few bytes (or none) -> sensor-only", async () => {
    for (const bytes of [0, 1000, null]) {
      const store = seed("struck_by_near_miss");
      store.events.get(id)!.ctx.clip!.bytes = bytes;
      const gemini = mockGemini(() => evilLlmOutput());
      await processClaimed(deps(store, cfg({ mode: "hybrid", anthropicModel: "claude-test" }), { gemini, claude: mockClaude(() => ({})) }), id);
      expect(gemini.calls).toHaveLength(0);
      expectSensorOnlyReport(store, /empty/);
    }
  });

  it("row looks fine but the downloaded file is tiny -> sensor-only", async () => {
    const store = seed("struck_by_near_miss");
    store.files.set(store.events.get(id)!.ctx.clip!.path, fakeClip(20));
    const gemini = mockGemini(() => evilLlmOutput());
    await processClaimed(deps(store, cfg({ mode: "gemini" }), { gemini }), id);
    expect(gemini.calls).toHaveLength(0);
    expectSensorOnlyReport(store, /downloaded file is 20 bytes/);
  });

  it("the threshold is configurable and a real-sized clip still goes through", async () => {
    const store = seed("struck_by_near_miss");
    const gemini = mockGemini(() => evilLlmOutput());
    await processClaimed(deps(store, cfg({ mode: "gemini" }), { gemini }), id);
    expect(gemini.calls).toHaveLength(1);
    expect(store.analyses.map((a) => a.model)).not.toContain("sensor-only");
    const store2 = seed("struck_by_near_miss");
    const gemini2 = mockGemini(() => evilLlmOutput());
    await processClaimed(deps(store2, cfg({ mode: "gemini", minClipBytes: 500_000 }), { gemini: gemini2 }), id);
    expect(gemini2.calls).toHaveLength(0);
  });
});

describe("Gemini file problems fall back to sensor-only", () => {
  it("file stuck in PROCESSING is given up after 120 s (fake clock, no real waiting)", async () => {
    let t = 0;
    const { ai, log } = fakeAi(["PROCESSING"]);
    const gemini = createGeminiClient("k", { ai, now: () => t, sleep: async (ms) => void (t += ms) });
    const store = seed("struck_by_near_miss");
    await processClaimed(deps(store, cfg({ mode: "gemini" }), { gemini }), id);
    expect(t).toBeGreaterThanOrEqual(120_000);
    expect(t).toBeLessThan(125_000);
    expect(log.generates).toBe(0);
    expect(log.uploads).toBe(1); // the video is not retried
    expect(log.deletes).toEqual(["files/abc"]);
    expectSensorOnlyReport(store, /still processing the video after 120 s/);
    expect(store.reports.get(id)!.report_json.confidence_and_gaps).toMatch(/could not be analysed/);
  });

  it("GEMINI_PROCESSING_TIMEOUT_S is honoured", async () => {
    let t = 0;
    const { ai } = fakeAi(["PROCESSING"]);
    const gemini = createGeminiClient("k", { ai, now: () => t, sleep: async (ms) => void (t += ms) });
    const store = seed("struck_by_near_miss");
    await processClaimed(deps(store, cfg({ mode: "gemini", geminiProcessingTimeoutS: 10 }), { gemini }), id);
    expect(t).toBeGreaterThanOrEqual(10_000);
    expect(t).toBeLessThan(15_000);
    expectSensorOnlyReport(store, /after 10 s/);
  });

  it("file goes PROCESSING -> FAILED: sensor-only, no retry, file deleted", async () => {
    const { ai, log } = fakeAi(["PROCESSING", "PROCESSING", "FAILED"]);
    const gemini = createGeminiClient("k", { ai, now: () => 0, sleep: noSleep });
    const store = seed("struck_by_near_miss");
    await processClaimed(deps(store, cfg({ mode: "gemini" }), { gemini }), id);
    expect(log.uploads).toBe(1);
    expect(log.generates).toBe(0);
    expect(log.deletes).toEqual(["files/abc"]);
    expectSensorOnlyReport(store, /FAILED/);
  });

  it("hybrid: a FAILED file -> Claude writes a no-video report from sensor data; the video is never sent to it", async () => {
    const { ai } = fakeAi(["FAILED"]);
    const claude = mockClaude(() => evilLlmOutput());
    const store = seed("struck_by_near_miss");
    await processClaimed(deps(store, cfg({ mode: "hybrid", anthropicModel: "claude-test" }), { gemini: createGeminiClient("k", { ai }), claude }), id);
    expect(claude.calls).toHaveLength(1);
    expect(claude.calls[0]!.prompt).toMatch(/no video analysis is available/);
    const ev = store.events.get(id)!;
    expect(ev.status, ev.error ?? "").toBe("ready");
    expect(store.reports.get(id)!.report_json.confidence_and_gaps).toMatch(/FAILED/);
    expect(store.analyses.find((a) => a.stage === "video_skipped")).toMatchObject({ model: "sensor-only" });
  });

  it("a provider that throws VideoUnusableError is not retried", async () => {
    let calls = 0;
    const gemini = {
      async generateWithVideo(): Promise<never> {
        calls++;
        throw new VideoUnusableError("Gemini could not process the video (file state FAILED)");
      },
    };
    const store = seed("struck_by_near_miss");
    await processClaimed(deps(store, cfg({ mode: "gemini" }), { gemini }), id);
    expect(calls).toBe(1);
    expectSensorOnlyReport(store, /FAILED/);
  });
});

describe("a hung provider call cannot stall the worker", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("Gemini generate hangs past LLM_CALL_TIMEOUT_S -> sensor-only report with the reason, no retry, file deleted", async () => {
    const { ai, log } = fakeAi(["ACTIVE"], { hangGenerate: true });
    const store = seed("struck_by_near_miss");
    const done = processClaimed(deps(store, cfg({ mode: "gemini", llmCallTimeoutS: 180 }), { gemini: createGeminiClient("k", { ai }) }), id);
    await vi.advanceTimersByTimeAsync(181_000);
    await done;
    expect(log.generates).toBe(1); // timeouts are not retried
    expect(log.deletes).toEqual(["files/abc"]);
    expectSensorOnlyReport(store, /Gemini generate call timed out after 180 s/);
  });

  it("a provider that ignores its own limit is stopped by the pipeline backstop", async () => {
    const gemini = { generateWithVideo: () => new Promise<never>(() => {}) };
    const store = seed("struck_by_near_miss");
    const done = processClaimed(deps(store, cfg({ mode: "gemini" }), { gemini }), id);
    await vi.advanceTimersByTimeAsync(1_200_000);
    await done;
    expectSensorOnlyReport(store, /timed out/);
  });

  it("Claude hangs in hybrid mode -> sensor-only report (no failure)", async () => {
    const claude = { generateJson: () => new Promise<never>(() => {}) };
    const store = seed("struck_by_near_miss");
    const gemini = mockGemini(() => ({ observations: [], contact_visible: "no", visibility_notes: "" }));
    const done = processClaimed(deps(store, cfg({ mode: "hybrid", anthropicModel: "claude-test" }), { gemini, claude }), id);
    await vi.advanceTimersByTimeAsync(181_000);
    await done;
    expect(gemini.calls).toHaveLength(1);
    expectSensorOnlyReport(store, /timed out after 180 s/);
  });
});

describe("attempt cap and reclaim", () => {
  const CFG = () => cfg({ mode: "gemini", staleAnalyzingMs: 10 * 60_000, maxAnalysisAttempts: 3 });

  it("an event whose worker keeps dying ends failed after 3 claims and is never reclaimed again", async () => {
    let now = 1_000_000;
    const store = seed("struck_by_near_miss");
    store.now = () => now;
    const d = deps(store, CFG());
    const ev = store.events.get(id)!;

    for (let claim = 1; claim <= 3; claim++) {
      expect(await store.claimNext(3)).toBe(id); // a worker claims it, then dies without a word
      expect(ev.analysis_attempts).toBe(claim);
      now += 11 * 60_000;
      // what runOnce does first: give up on exhausted events, then re-queue the rest
      await store.failExhausted(d.cfg.staleAnalyzingMs, 3);
      await store.reclaimStale(d.cfg.staleAnalyzingMs, 3);
      if (claim < 3) expect(ev.status).toBe("uploaded");
    }
    expect(ev.status).toBe("failed");
    expect(ev.error).toMatch(/Gave up after 3 analysis attempts/);
    expect(store.reports.size).toBe(0);

    for (let i = 0; i < 3; i++) {
      now += 60 * 60_000;
      expect(await runOnce(d)).toBe(false);
    }
    expect(ev.status).toBe("failed");
    expect(ev.analysis_attempts).toBe(3);
    expect(await store.claimNext(3)).toBeNull();
  });

  it("an exhausted event that is queued again is failed by claimNext, not run a 4th time", async () => {
    const store = seed("struck_by_near_miss");
    const ev = store.events.get(id)!;
    ev.analysis_attempts = 3;
    expect(await store.claimNext(3)).toBeNull();
    expect(ev.status).toBe("failed");
    expect(ev.error).toMatch(/Gave up after 3/);
  });

  it("reclaim leaves an event alone while its worker is alive", async () => {
    let now = 1_000_000;
    const store = seed("struck_by_near_miss");
    store.now = () => now;
    const ev = store.events.get(id)!;
    await store.claimNext(3);
    // a live worker touches the event at least every few minutes
    for (let i = 0; i < 5; i++) {
      now += 8 * 60_000;
      await store.touch(id);
      expect(await store.failExhausted(10 * 60_000, 3)).toEqual([]);
      expect(await store.reclaimStale(10 * 60_000, 3)).toEqual([]);
      expect(ev.status).toBe("analyzing");
    }
    expect(ev.analysis_attempts).toBe(1);
  });

  it("the worker heartbeat keeps updated_at fresh during one long provider call", async () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(1_000_000);
      const store = seed("struck_by_near_miss");
      expect(await store.claimNext(3)).toBe(id);
      const claimedAt = store.events.get(id)!.updated_at;
      const gemini = { generateWithVideo: () => new Promise((r) => setTimeout(() => r({ text: JSON.stringify(evilLlmOutput()), inputTokens: 1, outputTokens: 1, model: "gemini-test" }), 100_000)) };
      let seenMidCall = 0;
      const done = processClaimed(deps(store, cfg({ mode: "gemini", heartbeatMs: 30_000 }), { gemini }), id);
      await vi.advanceTimersByTimeAsync(95_000);
      seenMidCall = store.events.get(id)!.updated_at;
      await vi.advanceTimersByTimeAsync(10_000);
      await done;
      expect(seenMidCall).toBeGreaterThanOrEqual(claimedAt + 90_000);
      // and with the default threshold nothing would have been reclaimed meanwhile
      expect(store.events.get(id)!.status).toBe("ready");
    } finally {
      vi.useRealTimers();
    }
  });

  it("two workers cannot both claim the same event", async () => {
    const store = seed("struck_by_near_miss");
    const [a, b] = await Promise.all([store.claimNext(3), store.claimNext(3)]);
    expect([a, b].filter(Boolean)).toEqual([id]);
    expect(store.events.get(id)!.analysis_attempts).toBe(1);
  });
});

describe("config", () => {
  it("defaults", () => {
    const c = loadConfig({});
    expect(c).toMatchObject({ minClipBytes: 50_000, geminiProcessingTimeoutS: 120, llmCallTimeoutS: 180, maxAnalysisAttempts: 3, heartbeatMs: 30_000 });
    // 2 * (180 + 120 + 30 + 180) + 180 + 60 + 300 = 1560 s
    expect(c.staleAnalyzingMs).toBe(1_560_000);
    expect(c.staleAnalyzingMs).toBe(defaultStaleMs(180, 120));
  });

  it("is overridable and ignores junk", () => {
    const c = loadConfig({ WORKER_MIN_CLIP_BYTES: "1000", GEMINI_PROCESSING_TIMEOUT_S: "30", LLM_CALL_TIMEOUT_S: "abc", WORKER_STALE_ANALYZING_MS: "99", WORKER_MAX_ANALYSIS_ATTEMPTS: "5" });
    expect(c).toMatchObject({ minClipBytes: 1000, geminiProcessingTimeoutS: 30, llmCallTimeoutS: 180, staleAnalyzingMs: 99, maxAnalysisAttempts: 5 });
  });

  it("the default stale threshold exceeds the longest possible attempt", () => {
    const c = loadConfig({});
    const gemini = c.llmCallTimeoutS + c.geminiProcessingTimeoutS + 30 + c.llmCallTimeoutS;
    const longestTry = 2 * gemini + c.llmCallTimeoutS + 60;
    expect(c.staleAnalyzingMs / 1000).toBeGreaterThan(longestTry);
  });
});
