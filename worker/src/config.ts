export type ReportMode = "fixture" | "gemini" | "hybrid";

/**
 * USD per 1M tokens. RE-VERIFY on the provider pricing pages when keys are created (spec 3.3:
 * the Gemini figures came from third-party sources; Claude figures from the claude-api skill,
 * cached 2026-10-06). Thinking tokens bill as output on both providers.
 */
export const PRICES_USD_PER_MTOK: Record<string, { input: number; output: number }> = {
  "claude-opus-5-5": { input: 4, output: 20 },
  // Gemini Flash-tier estimate; override with GEMINI_PRICE_IN_PER_MTOK / GEMINI_PRICE_OUT_PER_MTOK.
  "gemini-default": { input: 1.5, output: 9 },
};
/** Used when a model has no price entry: deliberately pessimistic so the spend guard errs safe. */
export const UNKNOWN_MODEL_PRICE = { input: 5, output: 25 };

export interface WorkerConfig {
  mode: ReportMode;
  supabaseUrl: string;
  supabaseServiceRoleKey: string;
  geminiApiKey: string;
  geminiModel: string;
  anthropicApiKey: string;
  anthropicModel: string;
  spendCapUsd: number;
  pollIntervalMs: number;
  retryDelayMs: number;
  /**
   * Events stuck in `analyzing` longer than this (by updated_at) are re-queued: the worker that claimed them died.
   * The default is computed from the timeouts below (see defaultStaleMs), so a live attempt is never re-queued.
   */
  staleAnalyzingMs: number;
  /** A live worker touches events.updated_at this often while it works (and between stages). */
  heartbeatMs: number;
  /** Tries inside one claim (1 try + 1 retry). Timeouts and unusable video are never retried. */
  maxAttempts: number;
  /** Total claims per event, reclaims included. The next one marks the event failed. */
  maxAnalysisAttempts: number;
  /** A clip smaller than this (media.bytes, and the downloaded file) is treated as "no video". */
  minClipBytes: number;
  /** Gemini Files API: how long to wait for an uploaded video to leave PROCESSING. */
  geminiProcessingTimeoutS: number;
  /** Limit for each single provider network step (upload, generate, Claude request). */
  llmCallTimeoutS: number;
  /** Tries per Gemini call on transient errors (429/5xx/UNAVAILABLE/network), the first included. 1 = no retry. */
  geminiTransientRetries: number;
  /** Delay before the 2nd Gemini try; doubles after that, plus up to 25% jitter (10 s then 20 s: 30-38 s in all). */
  geminiTransientBackoffMs: number;
  geminiPrice: { input: number; output: number };
}

// Local `supabase start` defaults (the service-role key is the public demo key printed by the CLI).
const LOCAL_SUPABASE_URL = "http://127.0.0.1:54321";
// Well-known Supabase local-dev demo service_role JWT (same for every `supabase start`; not a secret).
// Newer CLI versions may print a different one: set SUPABASE_SERVICE_ROLE_KEY from `supabase status`.
const LOCAL_SERVICE_ROLE_KEY =
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImV4cCI6MTk4MzgxMjk5Nn0.EGIM96RAZx35lJzdJsyH-qQwv8Hdp7fsn3W0YpN81IU";

const POLL_STEP_S = 30; // one Files API status check is bounded at 30 s
const DOWNLOAD_ALLOWANCE_S = 60;
const STALE_MARGIN_S = 300;

/** Worst case for one Gemini call: upload + processing wait (+ a final 30 s status check) + generate. */
export function geminiCallBudgetS(llmCallTimeoutS: number, processingTimeoutS: number): number {
  return llmCallTimeoutS + processingTimeoutS + POLL_STEP_S + llmCallTimeoutS;
}

/**
 * Longest one try can run (hybrid, which is the longest path): clip download, a Gemini observation call, a Claude call,
 * then (if Claude fails) a Gemini-written fallback report, plus a margin. Defaults: 2*510 + 180 + 60 = 1260 s,
 * + 300 s margin = 1560 s (26 min).
 */
export function defaultStaleMs(llmCallTimeoutS: number, processingTimeoutS: number): number {
  const gemini = geminiCallBudgetS(llmCallTimeoutS, processingTimeoutS);
  return (2 * gemini + llmCallTimeoutS + DOWNLOAD_ALLOWANCE_S + STALE_MARGIN_S) * 1000;
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): WorkerConfig {
  const mode = (env.REPORT_MODE || "fixture").trim() as ReportMode;
  if (!["fixture", "gemini", "hybrid"].includes(mode)) {
    throw new Error(`REPORT_MODE must be fixture | gemini | hybrid (got "${mode}")`);
  }
  const num = (v: string | undefined, d: number) => {
    const n = v === undefined || v.trim() === "" ? NaN : Number(v);
    return Number.isFinite(n) ? n : d;
  };
  const posNum = (v: string | undefined, d: number) => {
    const n = num(v, d);
    return n > 0 ? n : d;
  };
  const geminiProcessingTimeoutS = posNum(env.GEMINI_PROCESSING_TIMEOUT_S, 120);
  const llmCallTimeoutS = posNum(env.LLM_CALL_TIMEOUT_S, 180);
  const dflt = PRICES_USD_PER_MTOK["gemini-default"]!;
  return {
    mode,
    supabaseUrl: env.SUPABASE_URL?.trim() || LOCAL_SUPABASE_URL,
    supabaseServiceRoleKey: env.SUPABASE_SERVICE_ROLE_KEY?.trim() || LOCAL_SERVICE_ROLE_KEY,
    geminiApiKey: env.GEMINI_API_KEY?.trim() || "",
    geminiModel: env.GEMINI_MODEL?.trim() || "",
    anthropicApiKey: env.ANTHROPIC_API_KEY?.trim() || "",
    anthropicModel: env.ANTHROPIC_MODEL?.trim() || "claude-opus-5-5",
    spendCapUsd: num(env.LLM_SPEND_CAP_USD, 25),
    pollIntervalMs: num(env.WORKER_POLL_MS, 2000),
    retryDelayMs: num(env.WORKER_RETRY_DELAY_MS, 3000),
    staleAnalyzingMs: num(env.WORKER_STALE_ANALYZING_MS, defaultStaleMs(llmCallTimeoutS, geminiProcessingTimeoutS)),
    heartbeatMs: posNum(env.WORKER_HEARTBEAT_MS, 30_000),
    maxAttempts: 2, // 1 try + 1 retry
    maxAnalysisAttempts: Math.max(1, Math.floor(posNum(env.WORKER_MAX_ANALYSIS_ATTEMPTS, 3))),
    minClipBytes: num(env.WORKER_MIN_CLIP_BYTES, 50_000),
    geminiProcessingTimeoutS,
    llmCallTimeoutS,
    geminiTransientRetries: Math.max(1, Math.floor(posNum(env.GEMINI_TRANSIENT_RETRIES, 3))),
    geminiTransientBackoffMs: num(env.GEMINI_TRANSIENT_BACKOFF_MS, 10_000),
    geminiPrice: {
      input: num(env.GEMINI_PRICE_IN_PER_MTOK, dflt.input),
      output: num(env.GEMINI_PRICE_OUT_PER_MTOK, dflt.output),
    },
  };
}

export function costUsd(
  price: { input: number; output: number },
  inputTokens: number,
  outputTokens: number,
): number {
  return (inputTokens * price.input + outputTokens * price.output) / 1_000_000;
}

export function priceFor(model: string, cfg: Pick<WorkerConfig, "geminiPrice">) {
  if (PRICES_USD_PER_MTOK[model]) return PRICES_USD_PER_MTOK[model]!;
  if (model.startsWith("gemini")) return cfg.geminiPrice;
  return UNKNOWN_MODEL_PRICE;
}
