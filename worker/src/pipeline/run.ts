import { SensorTraceSchema, type SensorTrace } from "@argus/shared";
import { costUsd, priceFor, type WorkerConfig } from "../config";
import { LLM_SCHEMA, OBSERVATION_SCHEMA, type Observations } from "../llm-schema";
import { OBSERVE_SYSTEM_PROMPT, WRITER_SYSTEM_PROMPT } from "../prompts/index";
import type { ProviderResult, Providers } from "../providers/types";
import type { EventContext, Store } from "../store/types";
import { assembleReport } from "./assemble";
import { buildFixtureDraft } from "./fixture";
import { eventPromptText, fmtOffset } from "./summary";

export interface Logger {
  info(msg: string): void;
  warn(msg: string): void;
  error(msg: string): void;
}
export const consoleLogger: Logger = {
  info: (m) => console.log(`[worker] ${m}`),
  warn: (m) => console.warn(`[worker] WARN ${m}`),
  error: (m) => console.error(`[worker] ERROR ${m}`),
};

export interface PipelineDeps {
  store: Store;
  cfg: WorkerConfig;
  providers: Providers;
  log?: Logger;
  sleep?: (ms: number) => Promise<void>;
}

/** An error retrying cannot fix. */
export class NonRetryableError extends Error {}
export class SpendCapError extends NonRetryableError {}

const defaultSleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

async function assertUnderCap(deps: PipelineDeps, stage: string): Promise<void> {
  const spent = await deps.store.totalCostUsd();
  if (spent >= deps.cfg.spendCapUsd) {
    throw new SpendCapError(
      `LLM spend cap reached: $${spent.toFixed(4)} spent of LLM_SPEND_CAP_USD=$${deps.cfg.spendCapUsd}; refusing paid call (${stage}). Raise LLM_SPEND_CAP_USD to continue.`,
    );
  }
}

function parseJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    // Tolerate a fenced block even though structured output should prevent it.
    const m = text.match(/```(?:json)?\s*([\s\S]*?)```/);
    if (m) return JSON.parse(m[1]!);
    throw new Error("model returned text that is not valid JSON");
  }
}

/** Spend guard -> provider call -> analyses row (always written once a paid call returned). */
async function paidCall(
  deps: PipelineDeps,
  ctx: EventContext,
  stage: string,
  model: string,
  call: () => Promise<ProviderResult>,
): Promise<unknown> {
  await assertUnderCap(deps, stage);
  const t = Date.now();
  const res = await call();
  const latency = Date.now() - t;
  let parsed: unknown;
  let parseError: string | null = null;
  try {
    parsed = parseJson(res.text);
  } catch (e) {
    parseError = (e as Error).message;
  }
  await deps.store.insertAnalysis({
    event_id: ctx.eventId,
    stage,
    model: res.model || model,
    input_tokens: res.inputTokens,
    output_tokens: res.outputTokens,
    // Priced by the requested model; a server-side fallback model may bill differently (see config.ts).
    cost_usd: costUsd(priceFor(model, deps.cfg), res.inputTokens, res.outputTokens),
    latency_ms: latency,
    output_json: parseError ? { parse_error: parseError, raw_text: res.text.slice(0, 20000) } : parsed,
  });
  if (parseError) throw new Error(`${stage}: ${parseError}`);
  return parsed;
}

function requireGemini(deps: PipelineDeps) {
  if (!deps.providers.gemini) throw new NonRetryableError("Gemini client is not configured (set GEMINI_API_KEY and GEMINI_MODEL)");
  if (!deps.cfg.geminiModel) {
    throw new NonRetryableError("GEMINI_MODEL is blank: set it to the Gemini Flash model name from your Google AI Studio account (verify the current name at signup)");
  }
  return deps.providers.gemini;
}

function clipRequired(ctx: EventContext) {
  if (!ctx.clip) throw new Error("event has no clip media row; cannot run a video model");
  return ctx.clip;
}

const OFFSET_NOTE = (pre: number) =>
  `The trigger moment t0 is ${pre} s into the clip. Report offset_s relative to t0 (so a moment 3 s before the trigger is -3).`;

async function geminiReport(deps: PipelineDeps, ctx: EventContext, trace: SensorTrace | null, video: Uint8Array): Promise<unknown> {
  const gemini = requireGemini(deps);
  const prompt = `${eventPromptText(ctx, trace)}\n\n${OFFSET_NOTE(ctx.event.window.pre_s)}\n\nWatch the attached clip and write the investigation analysis as JSON.`;
  return paidCall(deps, ctx, "report_draft", deps.cfg.geminiModel, () =>
    gemini.generateWithVideo({
      model: deps.cfg.geminiModel,
      systemInstruction: WRITER_SYSTEM_PROMPT,
      prompt,
      video,
      mimeType: "video/mp4",
      displayName: `${ctx.eventId}.mp4`,
      schema: LLM_SCHEMA,
    }),
  );
}

async function hybridReport(deps: PipelineDeps, ctx: EventContext, trace: SensorTrace | null, video: Uint8Array, log: Logger): Promise<unknown> {
  const gemini = requireGemini(deps);
  if (!deps.providers.claude) throw new NonRetryableError("Claude client is not configured (set ANTHROPIC_API_KEY)");
  const claude = deps.providers.claude;
  const pre = ctx.event.window.pre_s;

  // Stage 1: Gemini watches the video, returns visual facts only.
  const obsPrompt = `Event type: ${ctx.event.type}. The trigger moment t0 is ${pre} s into this clip (clip length about ${pre + ctx.event.window.post_s} s). Describe what you can see and hear, paying particular attention around t0.`;
  const obs = (await paidCall(deps, ctx, "video_analysis", deps.cfg.geminiModel, () =>
    gemini.generateWithVideo({
      model: deps.cfg.geminiModel,
      systemInstruction: OBSERVE_SYSTEM_PROMPT,
      prompt: obsPrompt,
      video,
      mimeType: "video/mp4",
      displayName: `${ctx.eventId}.mp4`,
      schema: OBSERVATION_SCHEMA,
    }),
  )) as Observations;

  // Stage 2: Claude writes the report from the observation timeline + sensor summary.
  const lines = (obs.observations ?? [])
    .map((o) => ({ ...o, offset: o.video_time_s - pre }))
    .sort((a, b) => a.offset - b.offset)
    .map((o) => `- ${fmtOffset(o.offset)} [${o.category}] ${o.observation}`);
  const prompt = [
    eventPromptText(ctx, trace),
    "",
    "Visual observations extracted from the clip by a video model (offsets relative to t0; facts only, no conclusions):",
    ...(lines.length ? lines : ["- (no observations returned)"]),
    `Physical contact with the hat visible or audible in the video: ${obs.contact_visible ?? "unclear"}.`,
    `Video visibility notes: ${obs.visibility_notes || "none"}`,
    "",
    "Write the investigation analysis as JSON. Mark facts from the observations above as source \"video\".",
  ].join("\n");

  try {
    return await paidCall(deps, ctx, "report_draft", deps.cfg.anthropicModel, () =>
      claude.generateJson({ model: deps.cfg.anthropicModel, system: WRITER_SYSTEM_PROMPT, prompt, schema: LLM_SCHEMA }),
    );
  } catch (e) {
    if (e instanceof NonRetryableError) throw e;
    // Spec 3.3: Gemini can write the report itself if stage 2 fails.
    log.warn(`Claude stage failed (${(e as Error).message}); falling back to a Gemini-written report`);
    return geminiReport(deps, ctx, trace, video);
  }
}

async function loadTrace(deps: PipelineDeps, ctx: EventContext): Promise<SensorTrace | null> {
  if (!ctx.trace) return null;
  const bytes = await deps.store.download(ctx.trace.path);
  return SensorTraceSchema.parse(JSON.parse(new TextDecoder().decode(bytes)));
}

async function attempt(deps: PipelineDeps, eventId: string, log: Logger): Promise<void> {
  const ctx = await deps.store.loadContext(eventId);
  if (ctx.event.device_class === "system") throw new NonRetryableError("system events do not produce reports");
  const trace = await loadTrace(deps, ctx);

  let draft: unknown;
  if (deps.cfg.mode === "fixture") {
    const t = Date.now();
    draft = buildFixtureDraft(ctx, trace);
    await deps.store.insertAnalysis({
      event_id: eventId,
      stage: "report_draft",
      model: "fixture",
      input_tokens: 0,
      output_tokens: 0,
      cost_usd: 0,
      latency_ms: Date.now() - t,
      output_json: draft,
    });
  } else {
    const video = await deps.store.download(clipRequired(ctx).path);
    draft =
      deps.cfg.mode === "gemini"
        ? await geminiReport(deps, ctx, trace, video)
        : await hybridReport(deps, ctx, trace, video, log);
  }

  const year = new Date(ctx.event.t0).getUTCFullYear();
  const reportNo = await deps.store.reportNoFor(eventId, year);
  const report = assembleReport(draft, ctx, reportNo);
  await deps.store.saveReport(eventId, report);
  await deps.store.markReady(eventId, report.classification_review, report.risk.score);
  log.info(`event ${eventId} ready: ${report.header.report_no} (${report.header.classification}, risk ${report.risk.score})`);
}

/** Process one already-claimed event: up to cfg.maxAttempts tries, then mark failed. Never throws. */
export async function processClaimed(deps: PipelineDeps, eventId: string): Promise<void> {
  const log = deps.log ?? consoleLogger;
  const sleep = deps.sleep ?? defaultSleep;
  let lastError = "unknown error";
  for (let n = 1; n <= deps.cfg.maxAttempts; n++) {
    try {
      await attempt(deps, eventId, log);
      return;
    } catch (e) {
      lastError = e instanceof Error ? e.message : String(e);
      log.warn(`event ${eventId} attempt ${n}/${deps.cfg.maxAttempts} failed: ${lastError}`);
      if (e instanceof NonRetryableError) break;
      if (n < deps.cfg.maxAttempts) await sleep(deps.cfg.retryDelayMs);
    }
  }
  try {
    await deps.store.markFailed(eventId, lastError);
  } catch (e) {
    log.error(`could not mark event ${eventId} failed: ${(e as Error).message}`);
  }
}

/** Claim and process a single event. Returns false when nothing was waiting. */
export async function runOnce(deps: PipelineDeps): Promise<boolean> {
  const id = await deps.store.claimNext();
  if (!id) return false;
  await processClaimed(deps, id);
  return true;
}

export async function runLoop(deps: PipelineDeps, signal: AbortSignal): Promise<void> {
  const log = deps.log ?? consoleLogger;
  const sleep = deps.sleep ?? defaultSleep;
  while (!signal.aborted) {
    try {
      const worked = await runOnce(deps);
      if (!worked) await sleep(deps.cfg.pollIntervalMs);
    } catch (e) {
      log.error(`poll error: ${(e as Error).message}`);
      await sleep(deps.cfg.pollIntervalMs);
    }
  }
}
