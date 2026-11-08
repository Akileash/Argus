import { readFileSync } from "node:fs";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { EVENT_TYPES, makeEvent, makeTrace, type EventType } from "@argus/shared";
import { loadConfig, type WorkerConfig } from "../src/config";
import type { Logger } from "../src/pipeline/run";
import type { ClaudeClient, GeminiClient, ProviderResult } from "../src/providers/types";
import { MemoryStore } from "../src/store/memory";
import type { EventContext } from "../src/store/types";

export const silent: Logger = { info() {}, warn() {}, error() {} };
export const noSleep = async () => {};

export function cfg(over: Partial<WorkerConfig> = {}): WorkerConfig {
  return { ...loadConfig({}), retryDelayMs: 0, geminiModel: "gemini-test", ...over };
}

const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
const validate = ajv.compile(JSON.parse(readFileSync(new URL("../../contracts/report.schema.json", import.meta.url), "utf8")));
export function jsonSchemaValid(r: unknown): { ok: boolean; errors: string } {
  const ok = validate(r) as boolean;
  return { ok, errors: ajv.errorsText(validate.errors) };
}

/** Big enough to pass the default WORKER_MIN_CLIP_BYTES (50 000). */
export const CLIP_TEST_BYTES = 200_000;
export function fakeClip(n: number): Uint8Array {
  const b = new Uint8Array(n);
  b.set([0, 0, 0, 24, 102, 116, 121, 112]);
  return b;
}

export function seed(type: EventType, store = new MemoryStore(), eventId = "6f1d2c1a-3b4e-4f5a-8b6c-7d8e9f0a1b2c"): MemoryStore {
  const event = makeEvent(type, eventId);
  const ctx: EventContext = {
    eventId,
    event,
    siteName: "Example Project - Edmonton",
    workerName: "J. Smith",
    crewName: "Framing A",
    supervisorName: "A. Tremblay",
    deviceSerial: "ARGUS-001",
    clip: event.media.some((m) => m.kind === "clip") ? { path: `org/${eventId}/clip.mp4`, contentType: "video/mp4", bytes: CLIP_TEST_BYTES, confirmed: true } : null,
    trace: event.media.some((m) => m.kind === "sensor_trace") ? { path: `org/${eventId}/trace.json`, contentType: "application/json" } : null,
  };
  store.addEvent(ctx);
  if (ctx.trace) store.files.set(ctx.trace.path, new TextEncoder().encode(JSON.stringify(makeTrace(type, eventId))));
  if (ctx.clip) store.files.set(ctx.clip.path, fakeClip(CLIP_TEST_BYTES));
  return store;
}

export const REPORTABLE = EVENT_TYPES.filter((t) => t !== "module_fallback");

/** A wrong-on-purpose model answer: bad location/classification/score/footer/sign-off, full header included. */
export function evilLlmOutput(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    schema_version: "9",
    header: { report_no: "HACKED", classification: "ppe_log", location_on_site: "Level 3, grid C4", site: "Wrong", worker: "Wrong", crew: "Wrong", supervisor: "Wrong", device: "Wrong", occurred_at: "2000-01-01T00:00:00Z" },
    summary: "A rear-approaching object passed close to the worker.",
    sequence_of_events: [{ offset_s: -1, description: "Object approaches from behind", source: "video" }],
    evidence: { clip_refs: [{ offset_s: -1, observation: "Object enters frame" }], key_sensor_values: [{ name: "closing", value: "4.2 m/s" }], environment: "Indicative only." },
    hazard: { category: "struck_by", energy_source: "Kinetic" },
    risk: { severity: 4, likelihood: 3, score: 99, severity_justification: "s", likelihood_justification: "l" },
    causes: { immediate: [{ kind: "substandard_condition", description: "d" }], root_five_whys: ["a", "b", "c"] },
    corrective_actions: [{ description: "Spotter", control_level: "administrative", suggested_owner_role: "Supervisor" }],
    regulatory_note: "Verify with your provincial OHS regulator.",
    confidence_and_gaps: "None.",
    classification_review: { suggested_class: "incident", reason: "Video shows contact with the hat." },
    sign_off: { signed_by: "Evil", signed_at: "2026-01-01T00:00:00Z", final_class: "incident", reclass_decision: "accepted" },
    footer: "Fully compliant with all regulations.",
    ...over,
  };
}

export interface Calls<T> { calls: T[] }
export function mockGemini(respond: (args: Parameters<GeminiClient["generateWithVideo"]>[0], n: number) => unknown, usage = { inputTokens: 10000, outputTokens: 2000 }): GeminiClient & Calls<Parameters<GeminiClient["generateWithVideo"]>[0]> {
  const calls: Parameters<GeminiClient["generateWithVideo"]>[0][] = [];
  return {
    calls,
    async generateWithVideo(args): Promise<ProviderResult> {
      calls.push(args);
      return { text: JSON.stringify(respond(args, calls.length)), ...usage, model: args.model };
    },
  };
}
export function mockClaude(respond: (args: Parameters<ClaudeClient["generateJson"]>[0], n: number) => unknown, usage = { inputTokens: 8000, outputTokens: 4000 }): ClaudeClient & Calls<Parameters<ClaudeClient["generateJson"]>[0]> {
  const calls: Parameters<ClaudeClient["generateJson"]>[0][] = [];
  return {
    calls,
    async generateJson(args): Promise<ProviderResult> {
      calls.push(args);
      return { text: JSON.stringify(respond(args, calls.length)), ...usage, model: args.model };
    },
  };
}
export const OBS = { observations: [{ video_time_s: 17, category: "object_motion", observation: "A pallet on a forklift moves into frame." }], contact_visible: "no", visibility_notes: "Clear." };
