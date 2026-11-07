import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { processClaimed } from "../src/pipeline/run";
import { sensorTimeline } from "../src/pipeline/summary";
import { MemoryStore } from "../src/store/memory";
import { cfg, jsonSchemaValid, mockClaude, mockGemini, noSleep, OBS, seed, silent, evilLlmOutput } from "./helpers";

const id = "6f1d2c1a-3b4e-4f5a-8b6c-7d8e9f0a1b2c";

/**
 * test/fixtures/device-*.json are real events + traces produced by the device's own code
 * (argus-cli run with the mock hardware: board_swing_rear and hat_drop_1m). Only the bulky sample columns are trimmed.
 */
function seedFromDevice(name: "struck_by_near_miss" | "fall"): MemoryStore {
  const fx = JSON.parse(readFileSync(new URL(`./fixtures/device-${name}.json`, import.meta.url), "utf8"));
  const store = seed(name);
  const ctx = store.events.get(id)!.ctx;
  ctx.event = { ...ctx.event, ...fx.event, event_id: id, media: ctx.event.media };
  store.files.set(ctx.trace!.path, new TextEncoder().encode(JSON.stringify({ ...fx.trace, event_id: id })));
  return store;
}

const run = (store: MemoryStore, c = cfg(), providers = {}) =>
  processClaimed({ store, cfg: c, providers, log: silent, sleep: noSleep }, id);

async function timeline(store: MemoryStore) {
  const ctx = store.events.get(id)!.ctx;
  const trace = JSON.parse(new TextDecoder().decode(store.files.get(ctx.trace!.path)!));
  return sensorTimeline(ctx, trace);
}

describe("sensor timeline from a real device trace", () => {
  it("struck-by: warnings collapse to one line, confirmed near miss and haptics read naturally", async () => {
    const store = seedFromDevice("struck_by_near_miss");
    const items = await timeline(store);
    const text = items.map((i) => i.text).join("\n");
    expect(items.filter((i) => /warned of an approaching object/.test(i.text))).toHaveLength(1);
    expect(text).toMatch(/warned of an approaching object 10 times between \+0\.0s and \+/);
    expect(text).toContain("closing at 2.5 m/s");
    expect(text).toContain("about 0.7 s to contact");
    expect(text).toContain("Near miss confirmed on rear sensor R2");
    expect(items.filter((i) => /Warning pulses/.test(i.text))).toHaveLength(1);
    expect(items.length).toBeLessThanOrEqual(5);
    expect(text).not.toMatch(/Detector recorded|n\/a|undefined|NaN/);
  });

  it("fall: free-fall duration, drop height and impact come from the device's field names", async () => {
    const store = seedFromDevice("fall");
    const items = await timeline(store);
    const text = items.map((i) => i.text).join("\n");
    expect(text).toContain("450 ms of free fall");
    expect(text).toContain("0.99 m");
    expect(text).toContain("14.0 g");
    expect(text).toContain("Confirmation buzz on both motors");
    expect(text).not.toMatch(/Detector recorded|n\/a|undefined|NaN|both motor motor/);
  });

  it("fixture report for a real device trace is schema-valid and reads like a draft", async () => {
    const store = seedFromDevice("struck_by_near_miss");
    await run(store);
    const ev = store.events.get(id)!;
    expect(ev.status, ev.error ?? "").toBe("ready");
    const r = store.reports.get(id)!.report_json;
    expect(jsonSchemaValid(r).ok).toBe(true);
    expect(JSON.stringify(r)).not.toMatch(/ixture/);
    expect(r.confidence_and_gaps).toContain("video clip was not reviewed");
  });
});

describe("clip-less events in gemini/hybrid mode get a sensor-only report", () => {
  for (const mode of ["gemini", "hybrid"] as const) {
    it(`${mode}: no clip row -> ready, no provider call, gap recorded`, async () => {
      const store = seed("struck_by_near_miss");
      const ctx = store.events.get(id)!.ctx;
      ctx.clip = null;
      const gemini = mockGemini(() => evilLlmOutput());
      const claude = mockClaude(() => evilLlmOutput());
      await run(store, cfg({ mode, anthropicModel: "claude-test" }), { gemini, claude });
      const ev = store.events.get(id)!;
      expect(ev.status, ev.error ?? "").toBe("ready");
      expect(gemini.calls).toHaveLength(0);
      expect(claude.calls).toHaveLength(0);
      const r = store.reports.get(id)!.report_json;
      expect(jsonSchemaValid(r).ok).toBe(true);
      expect(r.confidence_and_gaps).toContain("no video clip was received");
      expect(r.evidence.clip_refs).toEqual([]);
      expect(store.analyses[0]).toMatchObject({ stage: "report_draft", model: "sensor-only", cost_usd: 0 });
    });
  }

  it("a clip that exists still goes through the video model", async () => {
    const store = seed("struck_by_near_miss");
    const gemini = mockGemini(() => evilLlmOutput());
    await run(store, cfg({ mode: "gemini" }), { gemini });
    expect(gemini.calls).toHaveLength(1);
    expect(OBS).toBeTruthy();
  });

  it("hat_removed without a clip keeps the old rule (no report)", async () => {
    const store = seed("hat_removed");
    await run(store, cfg({ mode: "gemini" }), { gemini: mockGemini(() => evilLlmOutput()) });
    expect(store.events.get(id)!.status).toBe("failed");
    expect(store.reports.has(id)).toBe(false);
  });

  it("module_fallback never gets a report", async () => {
    const store = seed("module_fallback");
    await run(store, cfg({ mode: "gemini" }), {});
    expect(store.events.get(id)!.status).toBe("failed");
    expect(store.reports.has(id)).toBe(false);
  });
});
