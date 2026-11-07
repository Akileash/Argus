import { loadConfig } from "./config";
import { consoleLogger, runLoop } from "./pipeline/run";
import { createClaudeClient } from "./providers/claude";
import { createGeminiClient } from "./providers/gemini";
import type { Providers } from "./providers/types";
import { SupabaseStore } from "./store/supabase";

const cfg = loadConfig();
const providers: Providers = {};
if (cfg.mode !== "fixture") {
  if (!cfg.geminiModel) {
    throw new Error("REPORT_MODE=" + cfg.mode + " needs GEMINI_MODEL (verify the Gemini Flash model name at signup)");
  }
  providers.gemini = createGeminiClient(cfg.geminiApiKey);
  if (cfg.mode === "hybrid") providers.claude = createClaudeClient(cfg.anthropicApiKey);
}

const store = new SupabaseStore(cfg.supabaseUrl, cfg.supabaseServiceRoleKey);
const ac = new AbortController();
for (const sig of ["SIGINT", "SIGTERM"] as const) process.on(sig, () => ac.abort());

consoleLogger.info(`started: mode=${cfg.mode} supabase=${cfg.supabaseUrl} poll=${cfg.pollIntervalMs}ms cap=$${cfg.spendCapUsd}`);
await runLoop({ store, cfg, providers }, ac.signal);
consoleLogger.info("stopped");
