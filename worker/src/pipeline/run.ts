import { SensorTraceSchema, type SensorTrace } from "@argus/shared";
import { costUsd, geminiCallBudgetS, priceFor, type WorkerConfig } from "../config";
import { LlmTimeoutError, NonRetryableError, SpendCapError, VideoUnusableError } from "../errors";
import { TransientExhaustedError, describeTransient, maxBackoffMs } from "../providers/transient";
import { LLM_SCHEMA, OBSERVATION_SCHEMA, type Observations } from "../llm-schema";
import { OBSERVE_SYSTEM_PROMPT, WRITER_SYSTEM_PROMPT } from "../prompts/index";
import { withDeadline } from "../providers/timeout";
import type { CallTimeouts, ProviderResult, Providers } from "../providers/types";
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
  /** Set by processClaimed from cfg.eventBudgetMs: epoch ms after which no paid call may run. */
  eventDeadlineAt?: number;
}

export { LlmTimeoutError, NonRetryableError, SpendCapError, VideoUnusableError };

function timeoutsOf(cfg: WorkerConfig): CallTimeouts {
  return { callMs: cfg.llmCallTimeoutS * 1000, processingMs: cfg.geminiProcessingTimeoutS * 1000 };
}

/** Gemini limits plus the transient-retry policy; every retry is logged and refreshes the event heartbeat. */
function geminiTimeouts(deps: PipelineDeps, eventId: string): CallTimeouts {
  const log = deps.log ?? consoleLogger;
  return {
    ...timeoutsOf(deps.cfg),
    transient: { tries: deps.cfg.geminiTransientRetries, baseMs: deps.cfg.geminiTransientBackoffMs },
    onTransientRetry: ({ attempt, tries, delayMs, error }) => {
      log.warn(`event ${eventId}: Gemini try ${attempt}/${tries} failed (${describeTransient(error)}); retrying in ${Math.round(delayMs / 1000)} s`);
      void beat(deps, eventId);
    },
  };
}

/** Best-effort heartbeat: a failed touch must never fail the event. */
async function beat(deps: PipelineDeps, eventId: string): Promise<void> {
  try {
    await deps.store.touch(eventId);
  } catch (e) {
    (deps.log ?? consoleLogger).warn(`heartbeat for event ${eventId} failed: ${(e as Error).message}`);
  }
}

/**
 * Why this event has no video a model can use, or null when the clip row looks usable. Only a media row that is
 * confirmed and at least cfg.minClipBytes counts; everything else is "no clip" and gets the sensor-only report.
 */
export function clipGap(ctx: EventContext, cfg: Pick<WorkerConfig, "minClipBytes">): string | null {
  const c = ctx.clip;
  if (!c) return "clip missing";
  if (c.confirmed === false) return "clip upload was never confirmed";
  if (c.bytes === null || c.bytes === undefined || c.bytes < cfg.minClipBytes) {
    return `clip missing/empty: ${c.bytes ?? "unknown"} bytes, minimum ${cfg.minClipBytes}`;
  }
  return null;
}

const noVideoSentence = (reason: string) => `No usable video was received for this event (${reason}); this report is based on sensor data only.`;
const spendCapSentence = (detail: string) =>
  `AI spend cap reached (${detail}); this draft is from sensor data only. Review the video and complete the cause and actions before signing.`;
const unanalysedSentence = (reason: string) => `The video for this event could not be analysed (${reason}); this report is based on sensor data only.`;

/** Below this much budget left, a provider call is not worth starting. */
const MIN_CALL_MS = 2000;

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
  await beat(deps, ctx.eventId);
  const t = Date.now();
  // Backstop: the providers enforce their own limits, this also stops a mock or a future provider that ignores them.
  const isClaude = model === deps.cfg.anthropicModel && model !== deps.cfg.geminiModel;
  // Gemini may spend extra tries (each bounded by the call limit) plus backoff sleeps on transient errors.
  const retryExtraMs =
    (deps.cfg.geminiTransientRetries - 1) * deps.cfg.llmCallTimeoutS * 1000 + maxBackoffMs(deps.cfg.geminiTransientRetries, deps.cfg.geminiTransientBackoffMs);
  const budgetMs = isClaude
    ? deps.cfg.llmCallTimeoutS * 1000
    : geminiCallBudgetS(deps.cfg.llmCallTimeoutS, deps.cfg.geminiProcessingTimeoutS) * 1000 + retryExtraMs;
  let limitMs = budgetMs;
  if (deps.eventDeadlineAt !== undefined) {
    // Per-event wall-clock budget (hosted): never run past it. Hybrid keeps one Claude call's time in reserve for Gemini stages.
    const reserveMs = deps.cfg.mode === "hybrid" && !isClaude ? deps.cfg.llmCallTimeoutS * 1000 : 0;
    const remaining = deps.eventDeadlineAt - Date.now() - reserveMs;
    if (remaining < MIN_CALL_MS) {
      throw new LlmTimeoutError(`event time budget of ${Math.round(deps.cfg.eventBudgetMs / 1000)} s is used up; skipping ${stage} (${model}) call`);
    }
    limitMs = Math.min(budgetMs, remaining);
  }
  const res = await withDeadline(limitMs, `${stage} (${model}) call`, () => call());
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
  await beat(deps, ctx.eventId);
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
      timeouts: geminiTimeouts(deps, ctx.eventId),
    }),
  );
}

/** Hybrid bookkeeping: did the Claude writer already fail (so a no-video Claude retry is pointless)? */
interface HybridState {
  claudeFailed: boolean;
}

async function hybridReport(deps: PipelineDeps, ctx: EventContext, trace: SensorTrace | null, video: Uint8Array, log: Logger, st: HybridState): Promise<unknown> {
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
      timeouts: geminiTimeouts(deps, ctx.eventId),
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
      claude.generateJson({ model: deps.cfg.anthropicModel, system: WRITER_SYSTEM_PROMPT, prompt, schema: LLM_SCHEMA, timeouts: timeoutsOf(deps.cfg) }),
    );
  } catch (e) {
    if (e instanceof SpendCapError) throw e;
    st.claudeFailed = true;
    if (e instanceof LlmTimeoutError) throw e; // not retried; attempt() writes the sensor-only report
    if (e instanceof NonRetryableError || e instanceof VideoUnusableError) throw e;
    // Spec 3.3: Gemini can write the report itself if stage 2 fails.
    log.warn(`Claude stage failed (${(e as Error).message}); falling back to a Gemini-written report`);
    return geminiReport(deps, ctx, trace, video);
  }
}

/**
 * Hybrid fallback when the video could not be analysed: Claude writes the report from the sensor data alone.
 * The "video could not be analysed" sentence is added by the worker (not left to the model), and nothing in the
 * draft may claim video evidence.
 */
async function claudeSensorReport(deps: PipelineDeps, ctx: EventContext, trace: SensorTrace | null, sentence: string): Promise<unknown> {
  if (!deps.providers.claude) throw new NonRetryableError("Claude client is not configured (set ANTHROPIC_API_KEY)");
  const claude = deps.providers.claude;
  const prompt = [
    eventPromptText(ctx, trace),
    "",
    `IMPORTANT: no video analysis is available for this event. ${sentence}`,
    "Write the investigation analysis as JSON from the sensor data above only. Do not state or imply anything about what was seen or heard; every sequence_of_events entry has source \"sensor\", evidence.clip_refs is an empty array, and confidence_and_gaps says plainly that the video could not be analysed and why.",
  ].join("\n");
  const draft = await paidCall(deps, ctx, "report_draft", deps.cfg.anthropicModel, () =>
    claude.generateJson({ model: deps.cfg.anthropicModel, system: WRITER_SYSTEM_PROMPT, prompt, schema: LLM_SCHEMA, timeouts: timeoutsOf(deps.cfg) }),
  );
  if (draft === null || typeof draft !== "object" || Array.isArray(draft)) throw new Error("model output is not a JSON object");
  const d = draft as Record<string, any>;
  const gaps = typeof d.confidence_and_gaps === "string" ? d.confidence_and_gaps : "";
  d.confidence_and_gaps = gaps.includes(sentence) ? gaps : `${sentence} ${gaps}`.trim();
  d.evidence = { ...(d.evidence ?? {}), clip_refs: [] };
  if (Array.isArray(d.sequence_of_events)) d.sequence_of_events = d.sequence_of_events.map((x: any) => ({ ...x, source: "sensor" }));
  return d;
}

/** Why the video stage failed, when that is a case we degrade on (not an event failure); otherwise null. */
function videoStageGap(e: unknown): string | null {
  if (e instanceof VideoUnusableError) return e.message;
  if (e instanceof TransientExhaustedError) return e.message;
  if (e instanceof LlmTimeoutError) return e.message;
  return null;
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
  await beat(deps, eventId);

  // Only real video goes to a model. hat_removed / module_fallback keep their existing rule: no clip, no report.
  let draft: unknown = undefined;
  let gap: string | null = null; // set => sensor-only report, with this sentence in confidence_and_gaps
  let gapReason = "";
  const live = deps.cfg.mode !== "fixture";

  if (live) {
    const reason = clipGap(ctx, deps.cfg);
    if (reason) {
      // hat_removed keeps its existing rule: no clip, no report (it fails with this explicit error).
      if (ctx.event.type === "hat_removed") throw new Error("event has no clip media row; cannot run a video model");
      gapReason = reason;
      gap = noVideoSentence(reason);
    }
  }

  let video: Uint8Array | null = null;
  if (live && gap === null) {
    if (!ctx.clip) throw new Error("event has no clip media row; cannot run a video model");
    video = await deps.store.download(ctx.clip.path);
    await beat(deps, eventId);
    if (video.length < deps.cfg.minClipBytes) {
      if (ctx.event.type === "hat_removed") throw new Error("event has no clip media row; cannot run a video model");
      gapReason = `clip missing/empty: downloaded file is ${video.length} bytes, minimum ${deps.cfg.minClipBytes}`;
      gap = noVideoSentence(gapReason);
      video = null;
    }
  }
  let writtenBy: "claude" | null = null;
  if (live && video) {
    const st: HybridState = { claudeFailed: false };
    try {
      draft =
        deps.cfg.mode === "gemini"
          ? await geminiReport(deps, ctx, trace, video)
          : await hybridReport(deps, ctx, trace, video, log, st);
    } catch (e) {
      if (e instanceof SpendCapError) {
        // Over the cap: no more paid calls. A free sensor-only draft beats a failed event; the manager has the video.
        gapReason = "spend cap reached";
        gap = spendCapSentence(e.message);
        log.warn(`event ${eventId}: ${e.message}; writing a sensor-only draft`);
      }
      const why = e instanceof SpendCapError ? null : videoStageGap(e);
      if (why === null && !(e instanceof SpendCapError)) throw e;
      if (why !== null) {
      // The video cannot be analysed (file FAILED/PROCESSING, Gemini overloaded or hung, or Claude timed out).
      // Never an event failure: the report is written from sensor data. The video is not retried.
      gapReason = why;
      gap = unanalysedSentence(why);
      log.warn(`event ${eventId}: ${why}; writing a report from sensor data`);
      if (deps.cfg.mode === "hybrid" && deps.providers.claude && !st.claudeFailed) {
        try {
          draft = await claudeSensorReport(deps, ctx, trace, gap);
          writtenBy = "claude";
        } catch (ce) {
          if (ce instanceof SpendCapError) {
            gapReason = "spend cap reached";
            gap = spendCapSentence(ce.message);
            draft = undefined;
          } else {
          log.warn(`event ${eventId}: Claude could not write the sensor-only report (${(ce as Error).message}); using the sensor-only draft`);
          gapReason = `${why}; Claude was also unavailable (${(ce as Error).message})`;
          gap = unanalysedSentence(gapReason);
          }
        }
      }
      }
    }
  }
  if (writtenBy === "claude") {
    // paidCall already stored the report_draft row (model = Claude). This row records the skipped video stage; its
    // model "sensor-only" is what the dashboard keys on for the "No usable video" note.
    await deps.store.insertAnalysis({
      event_id: eventId,
      stage: "video_skipped",
      model: "sensor-only",
      input_tokens: 0,
      output_tokens: 0,
      cost_usd: 0,
      latency_ms: 0,
      output_json: { reason: gapReason, message: gap, report_written_by: "claude" },
    });
  }

  if (draft === undefined) {
    const sensorOnly = live;
    if (sensorOnly) log.warn(`event ${eventId}: ${gap}`);
    const t = Date.now();
    const noClip = { ...ctx, clip: sensorOnly ? null : ctx.clip };
    draft = buildFixtureDraft(noClip, trace, { videoMissing: !noClip.clip, videoGap: gap ?? undefined });
    await deps.store.insertAnalysis({
      event_id: eventId,
      stage: "report_draft",
      model: sensorOnly ? "sensor-only" : "fixture",
      input_tokens: 0,
      output_tokens: 0,
      cost_usd: 0,
      latency_ms: Date.now() - t,
      output_json: draft,
    });
    if (sensorOnly) {
      // The why, next to the draft, for anyone reading the analyses table.
      await deps.store.insertAnalysis({
        event_id: eventId,
        stage: "video_skipped",
        model: "sensor-only",
        input_tokens: 0,
        output_tokens: 0,
        cost_usd: 0,
        latency_ms: 0,
        output_json: { reason: gapReason, message: gap },
      });
    }
  }

  const year = new Date(ctx.event.t0).getUTCFullYear();
  const reportNo = await deps.store.reportNoFor(eventId, year);
  const report = assembleReport(draft, ctx, reportNo);
  await deps.store.saveReport(eventId, report);
  await deps.store.markReady(eventId, report.classification_review, report.risk.score);
  log.info(`event ${eventId} ready: ${report.header.report_no} (${report.header.classification}, risk ${report.risk.score})`);
}

/** Process one already-claimed event: up to cfg.maxAttempts tries, then mark failed. Never throws. */
export async function processClaimed(base: PipelineDeps, eventId: string): Promise<void> {
  const deps: PipelineDeps = base.cfg.eventBudgetMs > 0 ? { ...base, eventDeadlineAt: Date.now() + base.cfg.eventBudgetMs } : base;
  const log = deps.log ?? consoleLogger;
  const sleep = deps.sleep ?? defaultSleep;
  let lastError = "unknown error";
  // Keep updated_at fresh for as long as we work, so reclaimStale never re-queues a live event (stage-level touches
  // in attempt()/paidCall() cover the same ground; this timer covers the gaps inside a long provider call).
  const hb = deps.cfg.heartbeatMs > 0 ? setInterval(() => void beat(deps, eventId), deps.cfg.heartbeatMs) : undefined;
  try {
    await runTries();
  } finally {
    if (hb) clearInterval(hb);
  }

  async function runTries(): Promise<void> {
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
}

/** Claim and process a single event. Returns false when nothing was waiting. */
export async function runOnce(deps: PipelineDeps): Promise<boolean> {
  const log = deps.log ?? consoleLogger;
  const { staleAnalyzingMs: age, maxAnalysisAttempts: max } = deps.cfg;
  // Order matters: give up on exhausted events first, so they are not re-queued below.
  const gaveUp = await deps.store.failExhausted(age, max);
  if (gaveUp.length) log.error(`gave up on ${gaveUp.length} event(s) after ${max} attempts: ${gaveUp.join(", ")}`);
  const stale = await deps.store.reclaimStale(age, max);
  if (stale.length) log.warn(`re-queued ${stale.length} event(s) stuck in analyzing: ${stale.join(", ")}`);
  const id = await deps.store.claimNext(max);
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
