/**
 * Seed helper: give the seeded demo events a real video clip and a full-window sensor trace.
 *
 * The seed (supabase/seed.sql) creates `media` rows for its historical events but no storage objects, so the
 * dashboard shows "No clip" and "Sensor trace not available". This script generates a 30 s test clip (ffmpeg) and
 * a deterministic trace for each event from that event's OWN row (type, peak g, closing rate, time to contact,
 * range, sensor, free-fall time, drop height, environment), uploads both to the `event-media` bucket with the
 * service-role client and upserts the media rows (real size and sha256). Safe to run any number of times.
 *
 *   pnpm demo:seed-media                 # every seeded event that has media rows
 *   pnpm demo:seed-media <event-id> ...  # only these events
 *
 * Needs local Supabase running and ffmpeg on PATH (or FFMPEG=/path/to/ffmpeg). Credentials come from the
 * environment, then web/.env.local, then `supabase status -o env`.
 */
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { createClient } from "@supabase/supabase-js";
import { SensorTraceSchema, type SensorTrace } from "@argus/shared";

const FFMPEG = process.env.FFMPEG ?? "ffmpeg";
const REPO_ROOT = path.resolve(import.meta.dirname, "../..");

/** The MVP hat has two rear ultrasonic sensors, R2 and R3 (spec 3.2a). */
const SENSORS = ["R2", "R3"] as const;
type SensorId = (typeof SENSORS)[number];

function loadEnv() {
  try {
    process.loadEnvFile(path.resolve(import.meta.dirname, "../.env.local"));
  } catch {
    // env may already be set
  }
  if (process.env.NEXT_PUBLIC_SUPABASE_URL && process.env.SUPABASE_SERVICE_ROLE_KEY) return;
  try {
    const out = execFileSync("pnpm", ["exec", "supabase", "status", "-o", "env"], { cwd: REPO_ROOT, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] });
    const get = (k: string) => out.match(new RegExp(`^${k}="?([^"\\n]*)"?$`, "m"))?.[1];
    process.env.NEXT_PUBLIC_SUPABASE_URL ??= get("API_URL");
    process.env.SUPABASE_SERVICE_ROLE_KEY ??= get("SERVICE_ROLE_KEY");
  } catch {
    // reported below
  }
}

const round = (n: number, d = 3) => Math.round(n * 10 ** d) / 10 ** d;

function rng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** What the trace needs from an events row. */
export interface EventRow {
  id: string;
  type: "struck_by_near_miss" | "struck_by_contact" | "head_impact" | "fall" | "hat_knocked_off" | string;
  window_pre_s: number;
  window_post_s: number;
  peak_g: number | null;
  peak_g_saturated: boolean | null;
  closing_rate_mps: number | null;
  ttc_s: number | null;
  range_m: number | null;
  sensor_id: string | null;
  free_fall_ms: number | null;
  drop_height_m: number | null;
  co2_ppm: number | null;
  temp_c: number | null;
  rh_pct: number | null;
}

const SENSOR_MAX_M = 4; // HC-SR04 range limit
const RANGE_DT = 0.06; // one echo per ~60 ms, as on the device

/**
 * Full-window trace in the shape the device writes (device/argus/events/trace.py): same detection types and field
 * names, one confirmed `struck_by_*` event plus its repeated warnings, an ack buzz for impacts, hat-on changes.
 * Every number comes from the event row, so the chart, the key values and the report agree. Deterministic.
 */
export function buildTrace(ev: EventRow): SensorTrace {
  const pre = Number(ev.window_pre_s);
  const post = Number(ev.window_post_s);
  const rand = rng(Number.parseInt(ev.id.replace(/\D/g, "").slice(-6) || "1", 10));
  const struck = ev.type === "struck_by_near_miss" || ev.type === "struck_by_contact";
  const sat = !!ev.peak_g_saturated;
  const peakG = ev.peak_g ?? 0;
  const sensor: SensorId = (SENSORS as readonly string[]).includes(ev.sensor_id ?? "") ? (ev.sensor_id as SensorId) : "R3";

  // ---- IMU, 100 Hz
  const dt = 0.01;
  const n = Math.round((pre + post) / dt) + 1;
  const t = Array.from({ length: n }, (_, i) => round(-pre + i * dt, 2));
  const freeFallS = ev.type === "fall" ? (ev.free_fall_ms ?? 400) / 1000 : 0;
  // Impact time relative to t0: a contact lands after the approach, every other impact is the trigger itself.
  const closing = ev.closing_rate_mps ?? 0;
  const range0 = ev.range_m ?? 0;
  const tImpact = ev.type === "struck_by_contact" && closing > 0 ? round(Math.max(0.2, (range0 - 0.1) / closing), 2) : 0;
  const wobble = (x: number) => 1 + 0.03 * Math.sin(x * 7) + 0.02 * Math.sin(x * 23.3) + (rand() - 0.5) * 0.02;
  const hasImpact = ev.type !== "struck_by_near_miss" && peakG > 0;
  const hatOffAt = ev.type === "hat_knocked_off" ? tImpact + 0.2 : null;
  const a = t.map((x) => {
    const dtI = x - tImpact;
    if (ev.type === "fall" && x >= -freeFallS && x < 0) return round(0.05 + rand() * 0.04, 3);
    if (hasImpact && dtI >= 0 && dtI < 0.3) {
      const pulse = Math.exp(-dtI * 38) * (0.5 + 0.5 * Math.cos(dtI * 85)); // 1 at the peak sample
      return round(Math.min(sat ? 16 : peakG, wobble(x) + (peakG - 1) * pulse), 3);
    }
    if (ev.type === "fall" && x > 0 && x < 0.6) return round(1 + 2.2 * Math.exp(-x * 9) * Math.abs(Math.sin(x * 40)), 3); // rebound
    return round(wobble(x), 3);
  });
  const zeros = t.map(() => 0);
  const gyro = t.map((x) => round((hasImpact && Math.abs(x - tImpact) < 0.4 ? 90 : 4) * (rand() - 0.5), 2));

  // ---- Ranges: both rear sensors are present; only one sees the object.
  const rangeT: number[] = [];
  for (let x = -pre; x <= post + 1e-9; x += RANGE_DT) rangeT.push(round(x, 3));
  let rangeSeen: (number | null)[] = rangeT.map(() => null);
  let closingSeen: (number | null)[] = rangeT.map(() => null);
  const warnings: Array<{ t: number; range: number }> = [];
  if (struck && closing > 0 && range0 > 0) {
    // The object approaches at `closing`, so range(t) = range0 - closing * t. A near miss stops short and recedes.
    const tMin = (range0 - 0.4) / closing;
    const minRange = ev.type === "struck_by_contact" ? 0.1 : 0.4;
    const tEnd = ev.type === "struck_by_contact" ? tImpact : tMin;
    rangeSeen = rangeT.map((x) => {
      if (x < (range0 - SENSOR_MAX_M) / closing) return null;
      if (x <= tEnd) return round(Math.max(minRange, range0 - closing * x) + (rand() - 0.5) * 0.01, 2);
      if (ev.type === "struck_by_contact") return null;
      const back = minRange + closing * 0.5 * (x - tEnd);
      return back > SENSOR_MAX_M ? null : round(back, 2);
    });
    closingSeen = rangeT.map((x) => (x < (range0 - SENSOR_MAX_M) / closing || x > tEnd ? null : round(closing + 0.12 * Math.sin(x * 9), 2)));
    rangeT.forEach((x, i) => {
      if (x >= 0 && x <= Math.min(tEnd, 0.5) && rangeSeen[i] !== null) warnings.push({ t: x, range: rangeSeen[i]! });
    });
  }
  const ranges: SensorTrace["ranges"] = {};
  for (const s of SENSORS) {
    ranges[s] =
      s === sensor
        ? { t: rangeT, range_m: rangeSeen, closing_mps: closingSeen }
        : { t: rangeT, range_m: rangeT.map(() => null), closing_mps: rangeT.map(() => null) };
  }

  // ---- Detections, hat-on, haptics
  const detections: SensorTrace["detections"] = [];
  for (const w of warnings) {
    detections.push({ t: w.t, type: "struck_by_warning", sensor_id: sensor, closing_rate_mps: round(closing, 2), ttc_s: round(w.range / closing, 2), range_m: w.range });
  }
  if (struck) {
    detections.push({ t: 0, type: "struck_by_near_miss", sensor_id: sensor, closing_rate_mps: round(closing, 2), ttc_s: ev.ttc_s ?? round(range0 / closing, 2), range_m: range0 });
  }
  if (ev.type === "struck_by_contact" || ev.type === "head_impact") {
    detections.push({ t: round(tImpact, 2), type: "head_impact", sensor_id: null, peak_g: round(peakG, 2), ...(sat ? { peak_g_saturated: true } : {}) });
  }
  if (ev.type === "struck_by_contact") detections.push({ t: round(tImpact, 2), type: "struck_by_contact", sensor_id: sensor, closing_rate_mps: round(closing, 2), range_m: 0.1 });
  if (ev.type === "fall") detections.push({ t: 0, type: "fall", sensor_id: null, peak_g: round(peakG, 2), free_fall_ms: ev.free_fall_ms, drop_height_m: ev.drop_height_m, ...(sat ? { peak_g_saturated: true } : {}) });
  if (ev.type === "hat_knocked_off") detections.push({ t: 0, type: "hat_knocked_off", sensor_id: null, peak_g: round(peakG, 2) });

  const haptics: SensorTrace["haptics"] = [];
  if (struck) {
    // Back motor warning pulses that speed up as the object closes.
    [0.02, 0.14, 0.2, 0.26].forEach((ts, i) => haptics.push({ t: ts, motor: "back", pattern: `pulse_${8 + i}hz`, duration_s: round(i === 0 ? 0.118 : 0.06, 3) }));
  } else if (ev.type !== "hat_removed") {
    haptics.push({ t: 0.11, motor: "both", pattern: "ack_1500ms", duration_s: 1.504 });
  }

  const hat_on: SensorTrace["hat_on"] = [{ t: -pre, on: true }];
  if (hatOffAt !== null) hat_on.push({ t: round(hatOffAt, 2), on: false });

  const tofT = rangeT.filter((_, i) => i % 2 === 0);
  const peaks: NonNullable<SensorTrace["imu_peaks"]> = [];
  if (hasImpact) {
    // Full-rate peaks above 3 g (the 100 Hz columns can hide them).
    for (let k = 0; k < 6; k++) {
      const frac = Math.sin(((k + 1) / 7) * Math.PI);
      const g = k === 2 ? (sat ? 16 : peakG) : Math.max(3.1, peakG * frac * 0.8);
      peaks.push({ t: round(tImpact + k * 0.0025, 4), a_mag_g: round(g, 3), saturated: sat && k === 2 });
    }
  }
  return {
    schema_version: "1",
    event_id: ev.id,
    t_start_s: -pre,
    t_end_s: post,
    imu: { t, a_mag_g: a, ax: zeros, ay: zeros, az: a, gx: gyro, gy: zeros, gz: zeros },
    imu_peaks: peaks,
    ranges,
    tof: { t: tofT, range_mm: tofT.map((x) => (hatOffAt !== null && x > hatOffAt ? 410 : 62)) },
    hat_on,
    haptics,
    detections,
    env: { co2_ppm: ev.co2_ppm ?? 612, temp_c: ev.temp_c ?? 21.4, rh_pct: ev.rh_pct ?? 38 },
  };
}

function makeClip(dir: string, seconds: number): string {
  const clipPath = path.join(dir, "front.mp4");
  execFileSync(FFMPEG, ["-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30", "-t", String(seconds), "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p", "-movflags", "+faststart", clipPath]);
  return clipPath;
}

const COLUMNS =
  "id, org_id, type, window_pre_s, window_post_s, peak_g, peak_g_saturated, closing_rate_mps, ttc_s, range_m, sensor_id, free_fall_ms, drop_height_m, co2_ppm, temp_c, rh_pct";

async function main() {
  loadEnv();
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!url || !key) throw new Error("Supabase credentials not found: start local Supabase (pnpm db:start) or set NEXT_PUBLIC_SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY");
  const sb = createClient(url, key, { auth: { persistSession: false } });

  const ids = process.argv.slice(2);
  // Default: every event that already has media rows (the seeded historical events), never clip-less system events.
  let q = sb.from("events").select(`${COLUMNS}, media!inner(kind)`).in("type", ["struck_by_near_miss", "struck_by_contact", "head_impact", "fall", "hat_knocked_off"]);
  if (ids.length) q = q.in("id", ids);
  const { data, error } = await q;
  if (error) throw new Error(`load events: ${error.message}`);
  const events = (data ?? []) as unknown as Array<EventRow & { org_id: string }>;
  if (events.length === 0) throw new Error(ids.length ? `no matching event with media rows: ${ids.join(", ")}` : "no events with media rows found (run `pnpm db:reset` first)");

  const dir = mkdtempSync(path.join(tmpdir(), "argus-media-"));
  const clipSeconds = Math.max(...events.map((e) => Number(e.window_pre_s) + Number(e.window_post_s)));
  const clipBytes = readFileSync(makeClip(dir, clipSeconds));
  const sha = (b: Uint8Array) => createHash("sha256").update(b).digest("hex");

  for (const ev of events) {
    const trace = buildTrace(ev);
    SensorTraceSchema.parse(trace);
    const traceBytes = Buffer.from(JSON.stringify(trace));
    const files = [
      { kind: "clip" as const, filename: "front.mp4", body: clipBytes, contentType: "video/mp4", duration: Number(ev.window_pre_s) + Number(ev.window_post_s) },
      { kind: "sensor_trace" as const, filename: "sensor_trace.json", body: traceBytes, contentType: "application/json", duration: null },
    ];
    for (const f of files) {
      const storagePath = `${ev.org_id}/${ev.id}/${f.filename}`;
      const up = await sb.storage.from("event-media").upload(storagePath, f.body, { contentType: f.contentType, upsert: true });
      if (up.error) throw new Error(`upload ${storagePath}: ${up.error.message}`);
      const row = {
        event_id: ev.id,
        kind: f.kind,
        camera: f.kind === "clip" ? "front" : null,
        filename: f.filename,
        content_type: f.contentType,
        storage_path: storagePath,
        duration_s: f.duration,
        bytes: f.body.length,
        sha256: sha(f.body),
        confirmed: true,
      };
      const { error: mErr } = await sb.from("media").upsert(row, { onConflict: "event_id,filename" });
      if (mErr) throw new Error(`media row ${ev.id}/${f.filename}: ${mErr.message}`);
    }
    console.log(`attached clip + trace to ${ev.id} (${ev.type}, ${traceBytes.length} byte trace)`);
  }
  console.log(`done: ${events.length} event(s)`);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((e) => {
    console.error(e instanceof Error ? e.message : e);
    process.exit(1);
  });
}
