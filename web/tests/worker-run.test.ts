import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryStore, type PipelineDeps } from "@argus/worker";
import { cfg, seed, silent } from "../../worker/test/helpers";

const deps = vi.hoisted(() => ({ current: null as unknown }));
vi.mock("@/lib/worker/deps", () => ({ createWorkerDeps: () => deps.current }));

import { POST } from "@/app/api/worker/run/route";
import * as route from "@/app/api/worker/run/route";
import { isAuthorized } from "@/lib/worker/auth";
import { DRAIN_BUDGET_MS, drainEvents, eventWorstCaseMs, hostedWorkerEnv } from "@/lib/worker/process";
import { loadConfig } from "@argus/worker";

const SECRET = "s3cret-value-for-tests-0123456789";
const EVENT_IDS = [1, 2, 3, 4].map((n) => `6f1d2c1a-3b4e-4f5a-8b6c-7d8e9f0a1b0${n}`);

function req(headers: Record<string, string> = {}, method = "POST") {
  return new Request("http://app.test/api/worker/run", { method, headers });
}

function makeDeps(n: number, over: Parameters<typeof cfg>[0] = {}): PipelineDeps & { store: MemoryStore } {
  const store = new MemoryStore();
  for (const id of EVENT_IDS.slice(0, n)) seed("struck_by_near_miss", store, id);
  return { store, cfg: cfg({ mode: "fixture", ...over }), providers: {}, log: silent };
}

beforeEach(() => {
  process.env.WORKER_CRON_SECRET = SECRET;
  deps.current = makeDeps(0);
});

describe("POST /api/worker/run auth", () => {
  it("is Node runtime with maxDuration 300 and exports POST only", () => {
    expect(route.runtime).toBe("nodejs");
    expect(route.maxDuration).toBe(300);
    expect(Object.keys(route).filter((k) => ["GET", "PUT", "PATCH", "DELETE"].includes(k))).toEqual([]);
  });

  it("401 without Authorization", async () => {
    const res = await POST(req());
    expect(res.status).toBe(401);
  });

  it("401 with a wrong secret, a wrong scheme, or an empty bearer", async () => {
    for (const h of [`Bearer ${SECRET}x`, `Bearer wrong`, `Basic ${SECRET}`, SECRET, "Bearer "]) {
      expect((await POST(req({ authorization: h }))).status, h).toBe(401);
    }
  });

  it("401 for everyone when WORKER_CRON_SECRET is not configured", async () => {
    delete process.env.WORKER_CRON_SECRET;
    expect((await POST(req({ authorization: "Bearer " + SECRET }))).status).toBe(401);
    expect((await POST(req({ authorization: "Bearer " }))).status).toBe(401);
  });

  it("isAuthorized accepts only the exact secret", () => {
    expect(isAuthorized(req({ authorization: `Bearer ${SECRET}` }), SECRET)).toBe(true);
    expect(isAuthorized(req({ authorization: `bearer ${SECRET}` }), SECRET)).toBe(false);
  });
});

describe("POST /api/worker/run processing", () => {
  it("processes every waiting event with the in-memory store and returns {processed, remaining}", async () => {
    const d = makeDeps(3);
    deps.current = d;
    const res = await POST(req({ authorization: `Bearer ${SECRET}` }));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ processed: 3, remaining: 0 });
    expect([...d.store.events.values()].map((e) => e.status)).toEqual(["ready", "ready", "ready"]);
    expect(d.store.reports.size).toBe(3);
  });

  it("returns processed 0 when nothing is waiting", async () => {
    const res = await POST(req({ authorization: `Bearer ${SECRET}` }));
    expect(await res.json()).toEqual({ processed: 0, remaining: 0 });
  });

  it("500 (not a crash) when the worker cannot start", async () => {
    deps.current = {};
    const res = await POST(req({ authorization: `Bearer ${SECRET}` }));
    expect(res.status).toBe(500);
  });
});

describe("drainEvents time budget", () => {
  it("stops starting events once the budget is used up and reports what is left", async () => {
    const d = makeDeps(4);
    let clock = 0;
    const claim = d.store.claimNext.bind(d.store);
    d.store.claimNext = async (n) => {
      const id = await claim(n);
      if (id) clock += 100_000; // each event "takes" 100 s: elapsed is 0, 100, 200, 300 s when the next one would start
      return id;
    };
    const out = await drainEvents(d, { now: () => clock, budgetMs: DRAIN_BUDGET_MS });
    expect(out.processed).toBe(3);
    expect(out.remaining).toBe(1);
    expect([...d.store.events.values()].filter((e) => e.status === "uploaded")).toHaveLength(out.remaining);
  });

  it("does not start another event when it could not finish inside the 300 s function limit", async () => {
    // Each claimed event "takes" 100 s of fake time. Live worst case = 150 s event budget + 60 s = 210 s, so after
    // the first event (elapsed 100 s) 100 + 210 > 285 and the loop stops; fixture mode (worst 30 s) carries on.
    for (const [mode, expected] of [["gemini", 1], ["fixture", 3]] as const) {
      const d = makeDeps(3, { mode, eventBudgetMs: 150_000 });
      let clock = 0;
      const claim = d.store.claimNext.bind(d.store);
      d.store.claimNext = async (n) => {
        const id = await claim(n);
        if (id) clock += 100_000;
        return id;
      };
      const out = await drainEvents(d, { now: () => clock });
      expect(out.processed, mode).toBe(expected);
      expect(out.remaining, mode).toBe(3 - expected);
    }
  });

  it("always runs the first event, even when the clock is already past the guard", async () => {
    const d = makeDeps(2);
    let t = 0;
    const out = await drainEvents(d, { now: () => (t += 1), budgetMs: 1_000_000 });
    expect(out.processed).toBe(2);
  });
});

describe("hosted config", () => {
  it("applies the hosted timeouts when nothing is set, and lets real env values win; blanks do not", () => {
    const base = loadConfig(hostedWorkerEnv({ NEXT_PUBLIC_SUPABASE_URL: "https://x.supabase.co", GEMINI_MODEL: "" } as never));
    expect(base.supabaseUrl).toBe("https://x.supabase.co");
    expect(base.geminiProcessingTimeoutS).toBe(45);
    expect(base.llmCallTimeoutS).toBe(60);
    expect(base.geminiTransientRetries).toBe(2);
    expect(base.eventBudgetMs).toBe(150_000);
    expect(base.staleAnalyzingMs).toBe(360_000);
    const over = loadConfig(hostedWorkerEnv({ LLM_CALL_TIMEOUT_S: "30", GEMINI_PROCESSING_TIMEOUT_S: "" } as never));
    expect(over.llmCallTimeoutS).toBe(30);
    expect(over.geminiProcessingTimeoutS).toBe(45);
  });

  it("keeps the stale threshold above the function limit and the event worst case", () => {
    const c = loadConfig(hostedWorkerEnv({ REPORT_MODE: "hybrid" } as never));
    expect(c.staleAnalyzingMs).toBeGreaterThan(300_000);
    expect(c.staleAnalyzingMs).toBeGreaterThan(eventWorstCaseMs(c));
    expect(eventWorstCaseMs(c)).toBeLessThanOrEqual(300_000 - 60_000);
  });
});
