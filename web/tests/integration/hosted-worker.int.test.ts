import { createHash, randomUUID } from "node:crypto";
import { makeTrace } from "@argus/shared";
import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { describe, expect, it } from "vitest";
import { nearMissEvent, hatRemovedEvent } from "../helpers/fixtures";

/**
 * Hosted-mode end to end, against a running `next start` with REPORT_MODE=fixture and NO worker process:
 *   1. a device uploads an event through the API; /complete returns at once and the in-app after() run makes the report
 *   2. /api/worker/run (the cron path): 401 without the secret, and with it re-processes an event left in `uploaded`
 * Run through scripts/hosted/local-e2e.sh (it starts Supabase env, the app, and passes the env below).
 */
const run = process.env.ARGUS_INTEGRATION === "1" && !!process.env.WORKER_CRON_SECRET;
const BASE = process.env.WEB_BASE_URL ?? "http://127.0.0.1:3450";
const SUPA = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "http://127.0.0.1:54321";
const SERVICE = process.env.SUPABASE_SERVICE_ROLE_KEY ?? "";
const SECRET = process.env.WORKER_CRON_SECRET ?? "";
const auth = { authorization: "Bearer seed-token-ARG-0002", "content-type": "application/json" };
const sha = (b: Buffer) => createHash("sha256").update(b).digest("hex");

async function until<T>(what: string, fn: () => Promise<T | null | undefined | false>, ms = 20_000): Promise<T> {
  const t = Date.now();
  for (;;) {
    const v = await fn();
    if (v) return v;
    if (Date.now() - t > ms) throw new Error(`timed out waiting for ${what}`);
    await new Promise((r) => setTimeout(r, 250));
  }
}

describe.skipIf(!run)("hosted worker (integration, no worker process)", { timeout: 40_000 }, () => {
  // created lazily: the describe body runs even when the suite is skipped, and there is no key then
  let client: SupabaseClient | undefined;
  const db = () => (client ??= createClient(SUPA, SERVICE, { auth: { persistSession: false } }));
  const status = async (id: string) => (await db().from("events").select("status, analysis_attempts").eq("id", id).single()).data;

  async function uploadEvent(): Promise<{ id: string; completeMs: number }> {
    const clip = Buffer.from("fake-mp4-bytes-" + randomUUID());
    const id = randomUUID();
    const trace = Buffer.from(JSON.stringify(makeTrace("struck_by_near_miss", id)));
    const ev = nearMissEvent({ event_id: id, device_serial: "ARG-0002" });
    ev.media = [
      { kind: "clip", camera: "front", filename: "front.mp4", content_type: "video/mp4", bytes: clip.length, sha256: sha(clip), duration_s: 30 },
      { kind: "sensor_trace", filename: "sensor_trace.json", content_type: "application/json", bytes: trace.length, sha256: sha(trace) },
    ];
    const r1 = await fetch(`${BASE}/api/events`, { method: "POST", headers: auth, body: JSON.stringify(ev) });
    expect(r1.status).toBe(201);
    for (const u of (await r1.json()).uploads as { kind: string; signed_url: string }[]) {
      const body = u.kind === "clip" ? clip : trace;
      const put = await fetch(u.signed_url, { method: "PUT", headers: { "content-type": u.kind === "clip" ? "video/mp4" : "application/json" }, body: new Uint8Array(body) });
      expect(put.ok).toBe(true);
    }
    const t = Date.now();
    const done = await fetch(`${BASE}/api/events/${ev.event_id}/complete`, { method: "POST", headers: auth, body: JSON.stringify({ uploaded: ["front.mp4", "sensor_trace.json"] }) });
    const completeMs = Date.now() - t;
    expect(done.status).toBe(200);
    expect(await done.json()).toEqual({ status: "uploaded" });
    return { id: ev.event_id, completeMs };
  }

  it("the report is produced by the in-app after() run alone", async () => {
    const { id, completeMs } = await uploadEvent();
    console.log(`/complete answered in ${completeMs} ms`);
    await until("event ready", async () => (await status(id))?.status === "ready");
    const rep = await db().from("reports").select("status, report_no").eq("event_id", id).single();
    expect(rep.data?.status).toBe("draft");
    expect(rep.data?.report_no).toMatch(/^ARG-\d{4}-\d{4}$/);
    expect((await status(id))?.analysis_attempts).toBe(1);
  });

  it("POST /api/worker/run: 401 without or with a wrong secret, GET is 405", async () => {
    expect((await fetch(`${BASE}/api/worker/run`, { method: "POST" })).status).toBe(401);
    expect((await fetch(`${BASE}/api/worker/run`, { method: "POST", headers: { authorization: "Bearer wrong" } })).status).toBe(401);
    expect((await fetch(`${BASE}/api/worker/run`, { method: "GET" })).status).toBe(405);
  });

  it("POST /api/worker/run with the secret picks up an event left in `uploaded` (the cron path)", async () => {
    const { id } = await uploadEvent();
    await until("first run ready", async () => (await status(id))?.status === "ready");
    // pretend the in-app run never happened: put it back in the queue
    const back = await db().from("events").update({ status: "uploaded", analysis_attempts: 0 }).eq("id", id);
    expect(back.error).toBeNull();
    const res = await fetch(`${BASE}/api/worker/run`, { method: "POST", headers: { authorization: `Bearer ${SECRET}` } });
    expect(res.status).toBe(200);
    const body = (await res.json()) as { processed: number; remaining: number };
    expect(body.processed).toBeGreaterThanOrEqual(1);
    expect(body.remaining).toBe(0);
    expect((await status(id))?.status).toBe("ready");
  });

  it("hat_removed is ready at ingest and never goes near the worker", async () => {
    const ev = hatRemovedEvent();
    ev.event_id = randomUUID();
    ev.device_serial = "ARG-0002";
    const r = await fetch(`${BASE}/api/events`, { method: "POST", headers: auth, body: JSON.stringify(ev) });
    expect(r.status).toBe(201);
    expect(await r.json()).toMatchObject({ status: "ready", uploads: [] });
    const c = await fetch(`${BASE}/api/events/${ev.event_id}/complete`, { method: "POST", headers: auth, body: JSON.stringify({ uploaded: [] }) });
    expect(await c.json()).toEqual({ status: "ready" });
    await new Promise((r2) => setTimeout(r2, 1500));
    expect((await status(ev.event_id))?.analysis_attempts).toBe(0);
  });
});
