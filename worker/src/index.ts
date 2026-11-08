/** Library entry: the report worker as imported by the web app (the Vercel route). `pnpm --filter @argus/worker start` uses main.ts. */
export { loadConfig, defaultStaleMs, geminiCallBudgetS, type WorkerConfig, type ReportMode } from "./config";
export { runOnce, runLoop, processClaimed, consoleLogger, type Logger, type PipelineDeps } from "./pipeline/run";
export { createSupabaseStore, createProviders } from "./factories";
export { createGeminiClient } from "./providers/gemini";
export { createClaudeClient } from "./providers/claude";
export type { Providers, GeminiClient, ClaudeClient } from "./providers/types";
export { MemoryStore } from "./store/memory";
export type { Store } from "./store/types";
