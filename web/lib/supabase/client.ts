"use client";

import { createBrowserClient } from "@supabase/ssr";

/** Browser Supabase client (anon key, RLS applies). Use for Realtime subscriptions and client-side auth. */
export function createClient() {
  return createBrowserClient(process.env.NEXT_PUBLIC_SUPABASE_URL!, process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!);
}
