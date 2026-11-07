import "server-only";
import { createClient as createSupabaseClient, type SupabaseClient } from "@supabase/supabase-js";
import { publicEnv, serviceRoleKey } from "@/lib/env";

/** Service-role client. Bypasses RLS: server only, and only for the device API / registration. */
export function createAdminClient(): SupabaseClient {
  return createSupabaseClient(publicEnv().url, serviceRoleKey(), {
    auth: { persistSession: false, autoRefreshToken: false },
  });
}
