/**
 * Dev helper: generate a 30 s test clip (ffmpeg) and a matching full-window sensor trace, upload both to the
 * `event-media` bucket for one seeded event with the service-role client, and upsert the media rows.
 *
 *   pnpm --filter @argus/web exec tsx scripts/dev-attach-media.ts [event-id]
 *
 * Default event: the seeded draft with an AI reclass suggestion (ARG-2026-0009).
 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { createClient } from "@supabase/supabase-js";
import { SensorTraceSchema, type SensorTrace } from "@argus/shared";

try {
  process.loadEnvFile(path.resolve(import.meta.dirname, "../.env.local"));
} catch {
  // env may already be set
}

const DEFAULT_EVENT = "00000000-0000-4000-8000-000000000001";
const FFMPEG = process.env.FFMPEG ?? "/opt/homebrew/bin/ffmpeg";

function round(n: number, d = 3) {
  const k = 10 ** d;
  return Math.round(n * k) / k;
}

/** Full-window trace: 100 Hz IMU, one approaching-object track, an impact peak at t0. Deterministic. */
export function buildTrace(eventId: string, opts: { pre: number; post: number; sensor: "R1" | "R2" | "R3" | "R4"; peakG: number; saturated: boolean }): SensorTrace {
  const { pre, post, sensor, peakG, saturated } = opts;
  const dt = 0.01;
  const n = Math.round((pre + post) / dt) + 1;
  const t = Array.from({ length: n }, (_, i) => round(-pre + i * dt, 2));
  const wobble = (x: number) => 1 + 0.04 * Math.sin(x * 7) + 0.03 * Math.sin(x * 23.3);
  const a = t.map((x) => {
    if (x >= 0 && x < 0.25) return round(wobble(x) + (peakG - 1) * Math.exp(-(x * 40)) * Math.abs(Math.cos(x * 90)), 3);
    return round(wobble(x), 3);
  });
  const zeros = t.map(() => 0);
  // object enters sensing range at -2.2 s and closes at ~4.2 m/s until contact at t0
  const rangeT = t.filter((_, i) => i % 5 === 0); // 20 Hz
  const range_m = rangeT.map((x) => (x < -2.2 || x > 0.1 ? null : round(Math.max(0.1, 4.2 * (0 - x) + 0.1), 2)));
  const closing_mps = rangeT.map((x) => (x < -2.2 || x > 0.1 ? null : round(4.2 + 0.15 * Math.sin(x * 9), 2)));
  const ranges: SensorTrace["ranges"] = { [sensor]: { t: rangeT, range_m, closing_mps } };
  return {
    schema_version: "1",
    event_id: eventId,
    t_start_s: -pre,
    t_end_s: post,
    imu: { t, a_mag_g: a, ax: zeros, ay: zeros, az: a, gx: zeros, gy: zeros, gz: zeros },
    imu_peaks: [{ t: 0.02, a_mag_g: peakG, saturated }],
    ranges,
    tof: { t: rangeT, range_mm: rangeT.map(() => 62) },
    hat_on: [{ t: -pre, on: true }],
    haptics: [{ t: -1.1, motor: "back", pattern: "pulse_4hz", duration_s: 0.8 }],
    detections: [
      { t: -1.1, type: "struck_by_warning", sensor_id: sensor, closing_mps: 4.2, ttc_s: 1.1 },
      { t: 0.02, type: "head_impact", peak_g: peakG, saturated },
    ],
    env: { co2_ppm: 612, temp_c: 21.4, rh_pct: 38 },
  };
}

async function main() {
  const eventId = process.argv[2] ?? DEFAULT_EVENT;
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!url || !key) throw new Error("NEXT_PUBLIC_SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing (web/.env.local)");
  const sb = createClient(url, key, { auth: { persistSession: false } });

  const { data: ev, error } = await sb.from("events").select("id, org_id, window_pre_s, window_post_s, peak_g, peak_g_saturated, sensor_id").eq("id", eventId).single();
  if (error || !ev) throw new Error(`event ${eventId} not found: ${error?.message}`);
  const pre = Number(ev.window_pre_s);
  const post = Number(ev.window_post_s);

  const dir = mkdtempSync(path.join(tmpdir(), "argus-media-"));
  const clipPath = path.join(dir, "front.mp4");
  execFileSync(FFMPEG, ["-y", "-loglevel", "error", "-f", "lavfi", "-i", `testsrc2=size=1280x720:rate=30`, "-t", String(pre + post), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", clipPath]);

  const sensor = (["R1", "R2", "R3", "R4"] as const).find((s) => s === ev.sensor_id) ?? "R2";
  const trace = buildTrace(eventId, { pre, post, sensor, peakG: Number(ev.peak_g ?? 9.5), saturated: !!ev.peak_g_saturated });
  SensorTraceSchema.parse(trace);
  const tracePath = path.join(dir, "sensor_trace.json");
  writeFileSync(tracePath, JSON.stringify(trace));

  const files = [
    { kind: "clip" as const, filename: "front.mp4", file: clipPath, contentType: "video/mp4", duration: pre + post },
    { kind: "sensor_trace" as const, filename: "sensor_trace.json", file: tracePath, contentType: "application/json", duration: null },
  ];
  for (const f of files) {
    const storagePath = `${ev.org_id}/${eventId}/${f.filename}`;
    const body = readFileSync(f.file);
    const up = await sb.storage.from("event-media").upload(storagePath, body, { contentType: f.contentType, upsert: true });
    if (up.error) throw new Error(`upload ${storagePath}: ${up.error.message}`);
    const row = {
      event_id: eventId,
      kind: f.kind,
      camera: f.kind === "clip" ? "front" : null,
      filename: f.filename,
      content_type: f.contentType,
      storage_path: storagePath,
      duration_s: f.duration,
      bytes: body.length,
      confirmed: true,
    };
    const { error: mErr } = await sb.from("media").upsert(row, { onConflict: "event_id,filename" });
    if (mErr) throw new Error(`media row ${f.filename}: ${mErr.message}`);
    console.log(`attached ${storagePath} (${body.length} bytes)`);
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}
