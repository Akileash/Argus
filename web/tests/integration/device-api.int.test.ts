import { createHash, randomUUID } from "node:crypto";
import { createClient } from "@supabase/supabase-js";
import { describe, expect, it } from "vitest";
import { nearMissEvent, hatRemovedEvent } from "../helpers/fixtures";

/**
 * Hits the real routes against a running `next dev`/`next start` (WEB_BASE_URL, default http://localhost:3000)
 * and local Supabase. Run: ARGUS_INTEGRATION=1 pnpm --filter @argus/web test:int
 * Uses seeded device ARG-0002 (token seed-token-ARG-0002) and the seeded demo manager.
 */
const run = process.env.ARGUS_INTEGRATION === "1";
const BASE = process.env.WEB_BASE_URL ?? "http://localhost:3000";
const SUPA = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "http://127.0.0.1:54321";
const ANON = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "";
const auth = { authorization: "Bearer seed-token-ARG-0002", "content-type": "application/json" };

const sha = (b: Buffer) => createHash("sha256").update(b).digest("hex");

describe.skipIf(!run)("device API (integration)", () => {
  it("rejects missing/invalid token", async () => {
    expect((await fetch(`${BASE}/api/devices/me`)).status).toBe(401);
    expect((await fetch(`${BASE}/api/devices/me`, { headers: { authorization: "Bearer nope" } })).status).toBe(401);
  });

  it("GET /api/devices/me", async () => {
    const r = await fetch(`${BASE}/api/devices/me`, { headers: auth });
    expect(r.status).toBe(200);
    const j = await r.json();
    expect(j.serial).toBe("ARG-0002");
    expect(typeof j.server_time).toBe("string");
  });

  it("422 on invalid payload", async () => {
    const r = await fetch(`${BASE}/api/events`, { method: "POST", headers: auth, body: JSON.stringify({ nope: 1 }) });
    expect(r.status).toBe(422);
    expect((await r.json()).errors.length).toBeGreaterThan(0);
  });

  it("full flow: ingest -> signed PUT -> complete; repeat is idempotent", async () => {
    const clip = Buffer.from("fake-mp4-bytes-" + randomUUID());
    const trace = Buffer.from(JSON.stringify({ samples: [] }));
    const ev = nearMissEvent({ event_id: randomUUID(), device_serial: "ARG-0002" });
    ev.media = [
      { kind: "clip", camera: "front", filename: "front.mp4", content_type: "video/mp4", bytes: clip.length, sha256: sha(clip), duration_s: 30 },
      { kind: "sensor_trace", filename: "sensor_trace.json", content_type: "application/json", bytes: trace.length, sha256: sha(trace) },
    ];
    const post = () => fetch(`${BASE}/api/events`, { method: "POST", headers: auth, body: JSON.stringify(ev) });

    const r1 = await post();
    expect(r1.status).toBe(201);
    const j1 = await r1.json();
    expect(j1.status).toBe("uploading");
    expect(j1.uploads).toHaveLength(2);

    // complete before uploading -> 409
    const early = await fetch(`${BASE}/api/events/${ev.event_id}/complete`, {
      method: "POST",
      headers: auth,
      body: JSON.stringify({ uploaded: ["front.mp4", "sensor_trace.json"] }),
    });
    expect(early.status).toBe(409);

    const r2 = await post();
    expect(r2.status).toBe(200);
    const j2 = await r2.json();
    expect(j2.uploads).toHaveLength(2);

    for (const u of j2.uploads as { filename: string; kind: string; signed_url: string }[]) {
      const body = u.kind === "clip" ? clip : trace;
      const put = await fetch(u.signed_url, { method: "PUT", headers: { "content-type": u.kind === "clip" ? "video/mp4" : "application/json" }, body: new Uint8Array(body) });
      expect(put.ok).toBe(true);
    }

    const done = await fetch(`${BASE}/api/events/${ev.event_id}/complete`, {
      method: "POST",
      headers: auth,
      body: JSON.stringify({ uploaded: ["front.mp4", "sensor_trace.json"] }),
    });
    expect(done.status).toBe(200);
    expect(await done.json()).toEqual({ status: "uploaded" });

    const r3 = await post();
    expect(r3.status).toBe(200);
    expect((await r3.json()).uploads).toEqual([]);
  });

  it("hat_removed is ready immediately with no uploads", async () => {
    const ev = hatRemovedEvent();
    ev.event_id = randomUUID();
    ev.device_serial = "ARG-0002";
    const r = await fetch(`${BASE}/api/events`, { method: "POST", headers: auth, body: JSON.stringify(ev) });
    expect(r.status).toBe(201);
    expect(await r.json()).toMatchObject({ status: "ready", uploads: [] });
  });

  it("POST /api/devices/register: manager session pairs a new device; anonymous gets 401", async () => {
    expect((await fetch(`${BASE}/api/devices/register`, { method: "POST", body: JSON.stringify({ serial: "X-1" }) })).status).toBe(401);

    const sb = createClient(SUPA, ANON, { auth: { persistSession: false } });
    const { data, error } = await sb.auth.signInWithPassword({ email: "demo@argus.local", password: "argus-demo-2026" });
    expect(error).toBeNull();
    const session = data.session!;
    const { data: profile } = await sb.from("profiles").select("org_id").single();
    const cookieName = `sb-${new URL(SUPA).hostname.split(".")[0]}-auth-token`;
    const cookie = `${cookieName}=base64-${Buffer.from(JSON.stringify(session)).toString("base64url")}`;

    const serial = `INT-${Date.now()}`;
    const r = await fetch(`${BASE}/api/devices/register`, {
      method: "POST",
      headers: { cookie, "content-type": "application/json" },
      body: JSON.stringify({ serial, org_id: profile!.org_id }),
    });
    expect(r.status).toBe(201);
    const { device_token } = await r.json();
    const me = await fetch(`${BASE}/api/devices/me`, { headers: { authorization: `Bearer ${device_token}` } });
    expect(me.status).toBe(200);
    expect((await me.json()).serial).toBe(serial);
  });
});
