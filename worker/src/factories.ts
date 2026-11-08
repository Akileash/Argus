import type { WorkerConfig } from "./config";
import { createClaudeClient } from "./providers/claude";
import { createGeminiClient } from "./providers/gemini";
import type { Providers } from "./providers/types";
import { SupabaseStore } from "./store/supabase";
import type { Store } from "./store/types";

/** The Supabase-backed store (service-role client). */
export function createSupabaseStore(url: string, serviceRoleKey: string): Store {
  return new SupabaseStore(url, serviceRoleKey);
}

/** Provider clients for cfg.mode: none for fixture, Gemini for gemini, Gemini + Claude for hybrid. */
export function createProviders(cfg: WorkerConfig): Providers {
  const providers: Providers = {};
  if (cfg.mode !== "fixture") {
    if (!cfg.geminiModel) {
      throw new Error("REPORT_MODE=" + cfg.mode + " needs GEMINI_MODEL (verify the Gemini Flash model name at signup)");
    }
    providers.gemini = createGeminiClient(cfg.geminiApiKey);
    if (cfg.mode === "hybrid") providers.claude = createClaudeClient(cfg.anthropicApiKey);
  }
  return providers;
}
