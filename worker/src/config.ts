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
  maxAttempts: number;
  geminiPrice: { input: number; output: number };
}

// Local `supabase start` defaults (the service-role key is the public demo key printed by the CLI).
const LOCAL_SUPABASE_URL = "http://127.0.0.1:54321";
// Well-known Supabase local-dev demo service_role JWT (same for every `supabase start`; not a secret).
// Newer CLI versions may print a different one: set SUPABASE_SERVICE_ROLE_KEY from `supabase status`.
const LOCAL_SERVICE_ROLE_KEY =
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImV4cCI6MTk4MzgxMjk5Nn0.EGIM96RAZx35lJzdJsyH-qQwv8Hdp7fsn3W0YpN81IU";

export function loadConfig(env: NodeJS.ProcessEnv = process.env): WorkerConfig {
  const mode = (env.REPORT_MODE || "fixture").trim() as ReportMode;
  if (!["fixture", "gemini", "hybrid"].includes(mode)) {
    throw new Error(`REPORT_MODE must be fixture | gemini | hybrid (got "${mode}")`);
  }
  const num = (v: string | undefined, d: number) => {
    const n = v === undefined || v.trim() === "" ? NaN : Number(v);
    return Number.isFinite(n) ? n : d;
  };
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
    maxAttempts: 2, // 1 try + 1 retry
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
