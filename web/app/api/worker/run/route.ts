import { NextResponse } from "next/server";
import { isAuthorized } from "@/lib/worker/auth";
import { createWorkerDeps } from "@/lib/worker/deps";
import { drainEvents } from "@/lib/worker/process";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
/** Vercel Hobby maximum. lib/worker/process.ts keeps the actual work inside it. */
export const maxDuration = 300;

/** POST only (no GET export: Next answers 405). Called every minute by pg_cron, see supabase/hosted/cron.sql. */
export async function POST(request: Request) {
  if (!isAuthorized(request)) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  try {
    const result = await drainEvents(createWorkerDeps(), { log: (m) => console.log(`[worker] ${m}`) });
    return NextResponse.json(result);
  } catch (e) {
    console.error(`[worker] run failed: ${(e as Error).message}`);
    return NextResponse.json({ error: "worker_failed", message: (e as Error).message }, { status: 500 });
  }
}
