import { describe, expect, it } from "vitest";
import { LOCATION_PLACEHOLDER, REPORT_FOOTER } from "@argus/shared";
import { LLM_SCHEMA } from "../src/llm-schema";
import { processClaimed, runOnce } from "../src/pipeline/run";
import { MemoryStore } from "../src/store/memory";
import { OBS, REPORTABLE, cfg, evilLlmOutput, jsonSchemaValid, mockClaude, mockGemini, noSleep, seed, silent } from "./helpers";

const id = "6f1d2c1a-3b4e-4f5a-8b6c-7d8e9f0a1b2c";
const run = (store: MemoryStore, c = cfg(), providers = {}) =>
  processClaimed({ store, cfg: c, providers, log: silent, sleep: noSleep }, id);

describe("fixture mode end to end", () => {
  for (const type of REPORTABLE) {
    it(`${type}: schema-valid report, DB fields, ready`, async () => {
      const store = seed(type);
      expect(await runOnce({ store, cfg: cfg(), providers: {}, log: silent, sleep: noSleep })).toBe(true);
      const ev = store.events.get(id)!;
      expect(ev.status, ev.error ?? "").toBe("ready");
      const rep = store.reports.get(id)!;
      expect(rep.status).toBe("draft");
      expect(rep.report_json).toEqual(rep.llm_draft_json);
      const r = rep.report_json;
      const v = jsonSchemaValid(r);
      expect(v.ok, v.errors).toBe(true);
      expect(r.header.classification).toBe(ev.ctx.event.device_class);
      expect(r.header.location_on_site).toBe(LOCATION_PLACEHOLDER);
      expect(r.header).toMatchObject({ site: "Example Project - Edmonton", worker: "J. Smith", crew: "Framing A", supervisor: "A. Tremblay", device: "ARGUS-001", occurred_at: ev.ctx.event.t0 });
      expect(r.header.report_no).toMatch(/^ARG-2026-\d{4}$/);
      expect(r.risk.score).toBe(r.risk.severity * r.risk.likelihood);
      expect(ev.severity).toBe(r.risk.score);
      expect(r.sign_off).toEqual({ signed_by: null, signed_at: null, final_class: null, reclass_decision: null });
      expect(r.footer).toBe(REPORT_FOOTER);
      expect(r.sequence_of_events.length).toBeGreaterThan(0);
      expect(store.analyses).toHaveLength(1);
      expect(store.analyses[0]).toMatchObject({ model: "fixture", cost_usd: 0 });
    });
  }

  it("uses the actual metrics in the wording", async () => {
    const store = seed("struck_by_near_miss");
    await run(store);
    const text = JSON.stringify(store.reports.get(id)!.report_json);
    expect(text).toContain("4.2 m/s");
    expect(text).toContain("R2");
  });

  it("never quotes a saturated peak as exact", async () => {
    const store = seed("head_impact");
    await run(store);
    const text = JSON.stringify(store.reports.get(id)!.report_json);
    expect(text).toContain("\u2265 16 g (saturated)");
    expect(text).not.toMatch(/(?<!≥ )\b16(\.\d)? g/);
  });

  it("every saturated-capable event type quotes a lower bound, not an exact g", async () => {
    for (const type of ["struck_by_contact", "head_impact", "fall", "hat_knocked_off"] as const) {
      const store = seed(type);
      const ctx = store.events.get(id)!.ctx;
      ctx.event.metrics = { ...ctx.event.metrics, peak_g: 15.9, peak_g_saturated: true };
      const tr = JSON.parse(new TextDecoder().decode(store.files.get(ctx.trace!.path)!));
      for (const d of tr.detections) if (typeof d.peak_g === "number") Object.assign(d, { peak_g: 15.9, peak_g_saturated: true });
      store.files.set(ctx.trace!.path, new TextEncoder().encode(JSON.stringify(tr)));
      await run(store);
      const text = JSON.stringify(store.reports.get(id)!.report_json);
      expect(text, type).toContain("\u2265 16 g (saturated)");
      expect(text, type).not.toContain("15.9");
    }
  });

  it("is deterministic", async () => {
    const a = seed("fall");
    const b = seed("fall");
    await run(a);
    await run(b);
    expect(a.reports.get(id)!.report_json).toEqual(b.reports.get(id)!.report_json);
  });

  it("system events fail without retry", async () => {
    const store = seed("module_fallback");
    await run(store);
    expect(store.events.get(id)!.status).toBe("failed");
    expect(store.events.get(id)!.error).toContain("system events");
  });
});

describe("worker overrides win over the model", () => {
  const c = cfg({ mode: "gemini" });
  it("gemini mode", async () => {
    const store = seed("struck_by_near_miss");
    const gemini = mockGemini(() => evilLlmOutput());
    await run(store, c, { gemini });
    const ev = store.events.get(id)!;
    expect(ev.status, ev.error ?? "").toBe("ready");
    const r = store.reports.get(id)!.report_json;
    expect(r.header.location_on_site).toBe(LOCATION_PLACEHOLDER);
    expect(r.header.classification).toBe("near_miss");
    expect(r.header.report_no).not.toBe("HACKED");
    expect(r.header.worker).toBe("J. Smith");
    expect(r.risk.score).toBe(12);
    expect(r.sign_off.signed_by).toBeNull();
    expect(r.footer).toBe(REPORT_FOOTER);
    expect(r.schema_version).toBe("1");
    expect(jsonSchemaValid(r).ok).toBe(true);
    // advisory suggestion is kept and copied to the event
    expect(ev.llm_suggested_class).toBe("incident");
    expect(ev.llm_reclass_reason).toContain("contact");
    // the schema sent to the model excludes worker-owned fields
    const props = Object.keys((gemini.calls[0]!.schema as any).properties);
    expect(props).not.toContain("header");
    expect(props).not.toContain("footer");
    expect(gemini.calls[0]!.systemInstruction).toContain("Never infer or state where on the site");
    expect(gemini.calls[0]!.video.length).toBeGreaterThan(0);
    expect(store.analyses[0]).toMatchObject({ stage: "report_draft", model: "gemini-test", input_tokens: 10000, output_tokens: 2000 });
    expect(store.analyses[0]!.cost_usd).toBeCloseTo((10000 * 1.5 + 2000 * 9) / 1e6, 8);
  });

  it("agreeing or ppe_log suggestions are normalised to null", async () => {
    const s1 = seed("struck_by_contact");
    await run(s1, c, { gemini: mockGemini(() => evilLlmOutput()) });
    expect(s1.events.get(id)!.llm_suggested_class).toBeNull();
    expect(s1.reports.get(id)!.report_json.classification_review).toEqual({ suggested_class: null, reason: null });
    const s2 = seed("hat_removed");
    await run(s2, c, { gemini: mockGemini(() => evilLlmOutput()) }).catch(() => {});
    // hat_removed has no clip, so the video model cannot run: failure is the expected, explicit outcome
    expect(s2.events.get(id)!.status).toBe("failed");
  });

  it("hybrid mode: Gemini observes, Claude writes, worker overrides", async () => {
    const store = seed("struck_by_contact");
    const gemini = mockGemini(() => OBS);
    const claude = mockClaude(() => evilLlmOutput({ classification_review: { suggested_class: null, reason: null } }));
    await run(store, cfg({ mode: "hybrid", anthropicModel: "claude-opus-5-5" }), { gemini, claude });
    expect(store.events.get(id)!.status).toBe("ready");
    const r = store.reports.get(id)!.report_json;
    expect(r.header.location_on_site).toBe(LOCATION_PLACEHOLDER);
    expect(r.header.classification).toBe("incident");
    expect(claude.calls[0]!.prompt).toContain("-3.0s [object_motion] A pallet on a forklift");
    expect(claude.calls[0]!.model).toBe("claude-opus-5-5");
    expect(store.analyses.map((a) => a.stage)).toEqual(["video_analysis", "report_draft"]);
    const claudeRow = store.analyses[1]!;
    expect(claudeRow.cost_usd).toBeCloseTo((8000 * 4 + 4000 * 20) / 1e6, 8);
  });

  it("hybrid falls back to a Gemini-written report when Claude fails", async () => {
    const store = seed("struck_by_contact");
    const gemini = mockGemini((a) => (a.schema === LLM_SCHEMA ? evilLlmOutput() : OBS));
    const claude = { async generateJson(): Promise<never> { throw new Error("claude down"); } };
    await run(store, cfg({ mode: "hybrid" }), { gemini, claude });
    expect(store.events.get(id)!.status).toBe("ready");
    expect(store.analyses.map((a) => a.stage)).toEqual(["video_analysis", "report_draft"]);
  });
});

describe("spend cap", () => {
  it("refuses a paid call at the cap and marks the event failed, without retrying", async () => {
    const store = seed("struck_by_near_miss");
    store.baseCostUsd = 25;
    const gemini = mockGemini(() => evilLlmOutput());
    await run(store, cfg({ mode: "gemini", spendCapUsd: 25 }), { gemini });
    const ev = store.events.get(id)!;
    expect(ev.status).toBe("failed");
    expect(ev.error).toContain("LLM_SPEND_CAP_USD");
    expect(gemini.calls).toHaveLength(0);
  });

  it("hybrid: stops before stage 2 when stage 1 exhausts the cap", async () => {
    const store = seed("struck_by_near_miss");
    const gemini = mockGemini(() => OBS, { inputTokens: 1_000_000, outputTokens: 0 }); // $1.50
    const claude = mockClaude(() => evilLlmOutput());
    await run(store, cfg({ mode: "hybrid", spendCapUsd: 1 }), { gemini, claude });
    expect(store.events.get(id)!.status).toBe("failed");
    expect(store.events.get(id)!.error).toContain("spend cap");
    expect(claude.calls).toHaveLength(0);
    expect(store.analyses).toHaveLength(1);
  });

  it("fixture mode is free and ignores the cap", async () => {
    const store = seed("fall");
    store.baseCostUsd = 1000;
    await run(store, cfg({ spendCapUsd: 1 }));
    expect(store.events.get(id)!.status).toBe("ready");
  });
});

describe("retry then fail", () => {
  it("retries once and succeeds on the second attempt", async () => {
    const store = seed("struck_by_near_miss");
    const gemini = mockGemini((_a, n) => {
      if (n === 1) throw new Error("503 overloaded");
      return evilLlmOutput();
    });
    await run(store, cfg({ mode: "gemini" }), { gemini });
    expect(gemini.calls).toHaveLength(2);
    expect(store.events.get(id)!.status).toBe("ready");
  });

  it("fails after exactly 2 attempts with the error recorded", async () => {
    const store = seed("struck_by_near_miss");
    const gemini = mockGemini(() => {
      throw new Error("503 overloaded");
    });
    await run(store, cfg({ mode: "gemini" }), { gemini });
    const ev = store.events.get(id)!;
    expect(gemini.calls).toHaveLength(2);
    expect(ev.status).toBe("failed");
    expect(ev.error).toContain("503 overloaded");
    expect(store.reports.size).toBe(0);
  });

  it("schema-invalid model output fails the event (and is logged in analyses)", async () => {
    const store = seed("struck_by_near_miss");
    const gemini = mockGemini(() => evilLlmOutput({ risk: { severity: 9, likelihood: 3, severity_justification: "", likelihood_justification: "" } }));
    await run(store, cfg({ mode: "gemini" }), { gemini });
    expect(store.events.get(id)!.status).toBe("failed");
    expect(store.events.get(id)!.error).toMatch(/validation/);
    expect(store.analyses.length).toBe(2);
  });

  it("recovers from a transient storage failure", async () => {
    const store = seed("fall");
    store.failDownloads = 1;
    await run(store);
    expect(store.events.get(id)!.status).toBe("ready");
  });

  it("blank GEMINI_MODEL gives an actionable error", async () => {
    const store = seed("fall");
    await run(store, cfg({ mode: "gemini", geminiModel: "" }), { gemini: mockGemini(() => ({})) });
    expect(store.events.get(id)!.error).toContain("GEMINI_MODEL is blank");
  });
});

describe("stale analyzing recovery (crashed worker)", () => {
  it("re-queues events stuck in analyzing > 5 min and processes them", async () => {
    let now = 1_000_000;
    const store = seed("struck_by_near_miss");
    store.now = () => now;
    const ev = store.events.get(id)!;
    ev.status = "analyzing"; // a worker claimed it, then died
    ev.updated_at = now;
    // the stale threshold is configurable (the default is now computed from the timeouts, ~26 min)
    const deps = { store, cfg: cfg({ staleAnalyzingMs: 300_000 }), providers: {}, log: silent, sleep: noSleep };

    now += 4 * 60_000; // 4 min: a slow-but-alive worker may still own it
    expect(await runOnce(deps)).toBe(false);
    expect(ev.status).toBe("analyzing");

    now += 61_000; // > 5 min since updated_at
    expect(await runOnce(deps)).toBe(true);
    expect(ev.status, ev.error ?? "").toBe("ready");
    expect(store.reports.has(id)).toBe(true);
  });

  it("leaves fresh analyzing and failed events alone", async () => {
    const store = seed("fall");
    const ev = store.events.get(id)!;
    ev.status = "analyzing";
    expect(await store.reclaimStale(300_000)).toEqual([]);
    ev.status = "failed";
    store.now = () => Date.now() + 3_600_000;
    expect(await store.reclaimStale(300_000)).toEqual([]);
  });
});
