import { consoleLogger, createProviders, createSupabaseStore, loadConfig, type PipelineDeps } from "@argus/worker";
import { hostedWorkerEnv } from "./process";

/** Worker dependencies for this deployment, built from the environment (service-role key, REPORT_MODE, keys, timeouts). */
export function createWorkerDeps(env: NodeJS.ProcessEnv = process.env): PipelineDeps {
  const e = hostedWorkerEnv(env);
  if (!e.SUPABASE_URL) throw new Error("NEXT_PUBLIC_SUPABASE_URL (or SUPABASE_URL) must be set for the report worker");
  if (!env.SUPABASE_SERVICE_ROLE_KEY?.trim()) throw new Error("SUPABASE_SERVICE_ROLE_KEY must be set for the report worker");
  const cfg = loadConfig(e);
  return { store: createSupabaseStore(cfg.supabaseUrl, cfg.supabaseServiceRoleKey), cfg, providers: createProviders(cfg), log: consoleLogger };
}
