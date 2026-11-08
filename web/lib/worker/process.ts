/**
 * The report worker, run inside the web app (Vercel). Two callers share this module, in-process:
 *   - POST /api/worker/run (cron safety net, bearer-secret protected)
 *   - POST /api/events/[id]/complete, through after() (fast path: the report starts as soon as the upload is complete)
 *
 * Time budget (see docs/HOSTED.md for the math). Every number below assumes Vercel's 300 s function limit.
 */
import { runOnce, type PipelineDeps, type WorkerConfig } from "@argus/worker";

/** Vercel Hobby maxDuration for both routes. */
export const FUNCTION_MAX_S = 300;
/** No new event is started after this much time in one invocation. */
export const DRAIN_BUDGET_MS = 240_000;
/** Keep this much of the 300 s limit free, always. */
export const SAFETY_MARGIN_MS = 15_000;
/**
 * Time one event can take that the per-event budget does NOT cut: reading the event/trace/clip, writing results,
 * and a second try after an error. An assumption, not a measurement: 60 s.
 */
export const UNBUDGETED_MS = 60_000;

/** Hosted values for the timeouts; anything set in the environment wins. */
export const HOSTED_DEFAULTS: Record<string, string> = {
  GEMINI_PROCESSING_TIMEOUT_S: "45",
  LLM_CALL_TIMEOUT_S: "60",
  GEMINI_TRANSIENT_RETRIES: "2",
  GEMINI_TRANSIENT_BACKOFF_MS: "5000",
  WORKER_EVENT_BUDGET_S: "150",
  WORKER_HEARTBEAT_MS: "20000",
  // Above the 300 s function limit, so anything older than this was killed, never merely slow.
  WORKER_STALE_ANALYZING_MS: "360000",
};

/** process.env with blank values dropped, hosted defaults underneath, and SUPABASE_URL taken from the public URL. */
export function hostedWorkerEnv(env: Record<string, string | undefined> = process.env): Record<string, string | undefined> {
  const out: Record<string, string | undefined> = { ...HOSTED_DEFAULTS };
  for (const [k, v] of Object.entries(env)) if (v !== undefined && v.trim() !== "") out[k] = v;
  if (!out.SUPABASE_URL && out.NEXT_PUBLIC_SUPABASE_URL) out.SUPABASE_URL = out.NEXT_PUBLIC_SUPABASE_URL;
  return out;
}

/** Longest ONE event can take on this config, from claim to done. */
export function eventWorstCaseMs(cfg: Pick<WorkerConfig, "mode" | "eventBudgetMs">): number {
  if (cfg.mode === "fixture") return 30_000; // no model calls: reads and writes only
  if (cfg.eventBudgetMs > 0) return cfg.eventBudgetMs + UNBUDGETED_MS;
  return FUNCTION_MAX_S * 1000 - SAFETY_MARGIN_MS; // no per-event budget: assume one event can use everything
}

export interface DrainResult {
  processed: number;
  remaining: number;
}

export interface DrainOptions {
  budgetMs?: number;
  now?: () => number;
  log?: (msg: string) => void;
}

/**
 * Process waiting events one after another. Stops when none are waiting, or when starting another event could run
 * past the limits: elapsed >= budgetMs, or elapsed + (one event's worst case) > 300 s minus the margin.
 */
export async function drainEvents(deps: PipelineDeps, opts: DrainOptions = {}): Promise<DrainResult> {
  const now = opts.now ?? Date.now;
  const budgetMs = opts.budgetMs ?? DRAIN_BUDGET_MS;
  const hardMs = FUNCTION_MAX_S * 1000 - SAFETY_MARGIN_MS;
  const worstMs = eventWorstCaseMs(deps.cfg);
  const start = now();
  let processed = 0;
  for (;;) {
    const elapsed = now() - start;
    if (elapsed >= budgetMs) break;
    // The first event always runs: it is the one we were called for.
    if (processed > 0 && elapsed + worstMs > hardMs) break;
    const worked = await runOnce(deps);
    if (!worked) break;
    processed++;
  }
  const remaining = await deps.store.countWaiting();
  opts.log?.(`drain: processed=${processed} remaining=${remaining} elapsed=${Math.round((now() - start) / 1000)}s`);
  return { processed, remaining };
}
