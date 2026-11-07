import { createHash } from "node:crypto";
import type { SupabaseClient } from "@supabase/supabase-js";
import { SHORT_CIRCUIT_TYPES, type ArgusEvent } from "@/lib/contracts/event";
import type { AuthedDevice } from "./device-auth";
import { ApiError } from "./errors";
import { isSafeFilename, mediaPath, MEDIA_BUCKET, SIGNED_UPLOAD_TTL_S } from "./storage";

export interface UploadSlot {
  kind: "clip" | "sensor_trace";
  filename: string;
  signed_url: string;
  expires_at: string;
}

export interface IngestResult {
  httpStatus: 200 | 201;
  body: { event_id: string; status: string; uploads: UploadSlot[] };
}

const PENDING_STATUSES = new Set(["received", "uploading"]);

export function eventRow(ev: ArgusEvent, device: AuthedDevice) {
  const short = SHORT_CIRCUIT_TYPES.has(ev.type);
  const m = ev.metrics;
  return {
    id: ev.event_id,
    device_id: device.id,
    type: ev.type,
    device_class: ev.device_class,
    t0: ev.t0,
    detected_at: ev.detected_at,
    window_pre_s: ev.window.pre_s,
    window_post_s: ev.window.post_s,
    peak_g: m.peak_g ?? null,
    peak_g_saturated: m.peak_g_saturated ?? false,
    closing_rate_mps: m.closing_rate_mps ?? null,
    ttc_s: m.ttc_s ?? null,
    range_m: m.range_m ?? null,
    sensor_id: m.sensor_id ?? null,
    free_fall_ms: m.free_fall_ms ?? null,
    drop_height_m: m.drop_height_m ?? null,
    hat_on: m.hat_on,
    co2_ppm: ev.env?.co2_ppm ?? null,
    temp_c: ev.env?.temp_c ?? null,
    rh_pct: ev.env?.rh_pct ?? null,
    merged_triggers: ev.merged_triggers ?? [],
    firmware: ev.firmware,
    payload: ev,
    status: short ? "ready" : "received",
  };
}

function dbErr(e: { message: string } | null, what: string): void {
  if (e) throw new ApiError(500, "internal", `${what}: ${e.message}`);
}

/** Idempotent ingest (contracts/api.md #3). `db` is a service-role client. */
export async function ingestEvent(db: SupabaseClient, device: AuthedDevice, ev: ArgusEvent): Promise<IngestResult> {
  if (ev.device_serial !== device.serial) {
    throw new ApiError(403, "serial_mismatch", "device_serial does not match the authenticated device");
  }
  const short = SHORT_CIRCUIT_TYPES.has(ev.type);
  const media = short ? [] : ev.media;
  const seen = new Set<string>();
  for (const f of media) {
    if (!isSafeFilename(f.filename)) throw new ApiError(422, "invalid_media", `Unsafe filename: ${f.filename}`);
    if (seen.has(f.filename)) throw new ApiError(422, "invalid_media", `Duplicate filename: ${f.filename}`);
    seen.add(f.filename);
  }

  // on conflict (id) do nothing; triggers fill org_id/site_id/worker_id
  const ins = await db.from("events").upsert(eventRow(ev, device), { onConflict: "id", ignoreDuplicates: true }).select("id");
  dbErr(ins.error, "insert event");
  const created = (ins.data?.length ?? 0) > 0;

  const cur = await db.from("events").select("id, device_id, org_id, status").eq("id", ev.event_id).single();
  dbErr(cur.error, "load event");
  const row = cur.data as { id: string; device_id: string; org_id: string; status: string };
  if (row.device_id !== device.id) throw new ApiError(409, "conflict", "event_id belongs to another device");

  if (short) {
    return { httpStatus: created ? 201 : 200, body: { event_id: row.id, status: row.status, uploads: [] } };
  }

  if (media.length > 0) {
    const rows = media.map((f) => ({
      event_id: row.id,
      org_id: row.org_id,
      kind: f.kind,
      camera: f.camera ?? null,
      filename: f.filename,
      content_type: f.content_type,
      storage_path: mediaPath(row.org_id, row.id, f.filename),
      duration_s: f.duration_s ?? null,
      bytes: f.bytes,
      sha256: f.sha256,
    }));
    const r = await db.from("media").upsert(rows, { onConflict: "event_id,filename", ignoreDuplicates: true });
    dbErr(r.error, "insert media");
  }

  let status = row.status;
  if (status === "received") {
    const u = await db.from("events").update({ status: "uploading" }).eq("id", row.id).eq("status", "received");
    dbErr(u.error, "update status");
    status = "uploading";
  }

  const uploads: UploadSlot[] = [];
  if (PENDING_STATUSES.has(status)) {
    const pend = await db.from("media").select("kind, filename, storage_path").eq("event_id", row.id).eq("confirmed", false);
    dbErr(pend.error, "load media");
    const expires_at = new Date(Date.now() + SIGNED_UPLOAD_TTL_S * 1000).toISOString();
    for (const m of (pend.data ?? []) as { kind: UploadSlot["kind"]; filename: string; storage_path: string }[]) {
      const s = await db.storage.from(MEDIA_BUCKET).createSignedUploadUrl(m.storage_path, { upsert: true });
      if (s.error || !s.data) throw new ApiError(500, "storage", `signed url: ${s.error?.message ?? "no data"}`);
      uploads.push({ kind: m.kind, filename: m.filename, signed_url: s.data.signedUrl, expires_at });
    }
  }

  return { httpStatus: created ? 201 : 200, body: { event_id: row.id, status, uploads } };
}

/** Objects up to this size are read back and hashed on /complete (a 30 s 720p clip is ~10 MB; a trace ~0.2 MB). */
const MAX_VERIFY_BYTES = 64 * 1024 * 1024;

/** contracts/api.md #5 requires size AND sha256: download the object and compare with the declared hash. */
async function checksumProblem(
  db: SupabaseClient,
  m: { filename: string; sha256: string | null; storage_path: string },
  size: number | undefined,
): Promise<string | null> {
  if (!m.sha256 || (size ?? 0) > MAX_VERIFY_BYTES) return null;
  const dl = await db.storage.from(MEDIA_BUCKET).download(m.storage_path);
  if (dl.error || !dl.data) return `${m.filename}: could not read back to verify sha256`;
  const got = createHash("sha256").update(Buffer.from(await dl.data.arrayBuffer())).digest("hex");
  return got === m.sha256.toLowerCase() ? null : `${m.filename}: sha256 ${got} != declared ${m.sha256}`;
}

/** contracts/api.md #5. Verifies objects exist with the declared size and sha256, confirms media, sets status uploaded. */
export async function completeEvent(
  db: SupabaseClient,
  device: AuthedDevice,
  eventId: string,
  uploaded: string[],
): Promise<{ status: string }> {
  const ev = await db.from("events").select("id, org_id, device_id, status").eq("id", eventId).maybeSingle();
  dbErr(ev.error, "load event");
  const row = ev.data as { id: string; org_id: string; device_id: string; status: string } | null;
  if (!row || row.device_id !== device.id) throw new ApiError(404, "not_found", "Unknown event");
  if (!PENDING_STATUSES.has(row.status)) return { status: row.status }; // already completed / short-circuit: idempotent

  const mq = await db.from("media").select("id, filename, bytes, sha256, storage_path, confirmed").eq("event_id", row.id);
  dbErr(mq.error, "load media");
  const all = (mq.data ?? []) as { id: string; filename: string; bytes: number | null; sha256: string | null; storage_path: string; confirmed: boolean }[];

  const declared = new Set(uploaded);
  const problems: string[] = [];
  const toConfirm: string[] = [];
  for (const m of all) {
    if (m.confirmed) continue;
    if (!declared.has(m.filename)) {
      problems.push(`${m.filename}: not reported as uploaded`);
      continue;
    }
    const folder = m.storage_path.slice(0, m.storage_path.lastIndexOf("/"));
    const l = await db.storage.from(MEDIA_BUCKET).list(folder, { search: m.filename, limit: 100 });
    if (l.error) throw new ApiError(500, "storage", `list: ${l.error.message}`);
    const obj = (l.data ?? []).find((o) => o.name === m.filename);
    const size = obj?.metadata?.size as number | undefined;
    if (!obj) problems.push(`${m.filename}: object not found`);
    else if (m.bytes != null && size !== Number(m.bytes)) problems.push(`${m.filename}: size ${size} != declared ${m.bytes}`);
    else {
      const bad = await checksumProblem(db, m, size);
      if (bad) problems.push(bad);
      else toConfirm.push(m.id);
    }
  }
  if (problems.length) {
    throw new ApiError(409, "upload_incomplete", problems.join("; "), { problems });
  }

  if (toConfirm.length) {
    const c = await db.from("media").update({ confirmed: true }).in("id", toConfirm);
    dbErr(c.error, "confirm media");
  }
  const u = await db.from("events").update({ status: "uploaded" }).eq("id", row.id).in("status", ["received", "uploading"]);
  dbErr(u.error, "update status");
  return { status: "uploaded" };
}
