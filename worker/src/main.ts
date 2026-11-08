import { loadConfig } from "./config";
import { createProviders, createSupabaseStore } from "./factories";
import { consoleLogger, runLoop } from "./pipeline/run";

const cfg = loadConfig();
const providers = createProviders(cfg);
const store = createSupabaseStore(cfg.supabaseUrl, cfg.supabaseServiceRoleKey);
const ac = new AbortController();
for (const sig of ["SIGINT", "SIGTERM"] as const) process.on(sig, () => ac.abort());

consoleLogger.info(`started: mode=${cfg.mode} supabase=${cfg.supabaseUrl} poll=${cfg.pollIntervalMs}ms cap=$${cfg.spendCapUsd}`);
await runLoop({ store, cfg, providers }, ac.signal);
consoleLogger.info("stopped");
