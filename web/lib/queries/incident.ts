import "server-only";
import { SensorTraceSchema, STORAGE_BUCKET, type SensorTrace } from "@argus/shared";
import { createClient } from "@/lib/supabase/server";
import type { ReportJson } from "@/lib/report/model";

export type EventClass = "near_miss" | "incident" | "ppe_log" | "system";

export interface IncidentEvent {
  id: string;
  type: string;
  status: string;
  error: string | null;
  t0: string;
  window_pre_s: number;
  window_post_s: number;
  device_class: EventClass;
  llm_suggested_class: EventClass | null;
  llm_reclass_reason: string | null;
  final_class: EventClass | null;
  severity: number | null;
  peak_g: number | null;
  peak_g_saturated: boolean;
  closing_rate_mps: number | null;
  ttc_s: number | null;
  range_m: number | null;
  sensor_id: string | null;
  hat_on: boolean | null;
  co2_ppm: number | null;
  temp_c: number | null;
  rh_pct: number | null;
}

export interface IncidentReportRow {
  id: string;
  report_no: string;
  status: "draft" | "signed";
  edited: boolean;
  llm_draft_json: ReportJson;
  report_json: ReportJson;
  signed_off_by: string | null;
  signed_off_at: string | null;
  updated_at: string;
}

export type TraceState = "ok" | "missing" | "invalid";

export interface IncidentData {
  event: IncidentEvent;
  workerName: string | null;
  crewName: string | null;
  supervisorName: string | null;
  siteName: string | null;
  deviceSerial: string | null;
  report: IncidentReportRow | null;
  clipUrl: string | null;
  clipDurationS: number | null;
  trace: SensorTrace | null;
  traceState: TraceState;
}

const num = (v: unknown): number | null => (v == null ? null : Number(v));

/** Load everything the incident detail / print pages need. RLS-scoped; returns null when the event is not visible. */
export async function getIncident(id: string, opts: { withMedia?: boolean } = {}): Promise<IncidentData | null> {
  if (!/^[0-9a-f-]{36}$/i.test(id)) return null;
  const supabase = await createClient();

  const { data: ev } = await supabase
    .from("events")
    .select(
      "*, workers(full_name, crews(name, supervisor_name)), sites(name), devices(serial)",
    )
    .eq("id", id)
    .maybeSingle();
  if (!ev) return null;

  const [{ data: rep }, { data: media }] = await Promise.all([
    supabase
      .from("reports")
      .select("id, report_no, status, edited, llm_draft_json, report_json, signed_off_by, signed_off_at, updated_at")
      .eq("event_id", id)
      .maybeSingle(),
    supabase.from("media").select("kind, storage_path, duration_s, confirmed").eq("event_id", id),
  ]);

  const worker = ev.workers as { full_name: string; crews: { name: string; supervisor_name: string | null } | null } | null;
  const event: IncidentEvent = {
    id: ev.id,
    type: ev.type,
    status: ev.status,
    error: ev.error,
    t0: ev.t0,
    window_pre_s: Number(ev.window_pre_s),
    window_post_s: Number(ev.window_post_s),
    device_class: ev.device_class,
    llm_suggested_class: ev.llm_suggested_class,
    llm_reclass_reason: ev.llm_reclass_reason,
    final_class: ev.final_class,
    severity: ev.severity,
    peak_g: num(ev.peak_g),
    peak_g_saturated: ev.peak_g_saturated,
    closing_rate_mps: num(ev.closing_rate_mps),
    ttc_s: num(ev.ttc_s),
    range_m: num(ev.range_m),
    sensor_id: ev.sensor_id,
    hat_on: ev.hat_on,
    co2_ppm: num(ev.co2_ppm),
    temp_c: num(ev.temp_c),
    rh_pct: num(ev.rh_pct),
  };

  let clipUrl: string | null = null;
  let clipDurationS: number | null = null;
  let trace: SensorTrace | null = null;
  let traceState: TraceState = "missing";

  if (opts.withMedia !== false) {
    const clip = media?.find((m) => m.kind === "clip");
    const tr = media?.find((m) => m.kind === "sensor_trace");
    if (clip) {
      const { data } = await supabase.storage.from(STORAGE_BUCKET).createSignedUrl(clip.storage_path, 3600);
      // createSignedUrl succeeds even when the object does not exist; verify cheaply with a HEAD request.
      if (data?.signedUrl && (await objectExists(data.signedUrl))) {
        clipUrl = data.signedUrl;
        clipDurationS = num(clip.duration_s);
      }
    }
    if (tr) {
      const { data: blob } = await supabase.storage.from(STORAGE_BUCKET).download(tr.storage_path);
      if (blob) {
        try {
          const parsed = SensorTraceSchema.safeParse(JSON.parse(await blob.text()));
          if (parsed.success) {
            trace = parsed.data;
            traceState = "ok";
          } else traceState = "invalid";
        } catch {
          traceState = "invalid";
        }
      }
    }
  }

  return {
    event,
    workerName: worker?.full_name ?? null,
    crewName: worker?.crews?.name ?? null,
    supervisorName: worker?.crews?.supervisor_name ?? null,
    siteName: (ev.sites as { name: string } | null)?.name ?? null,
    deviceSerial: (ev.devices as { serial: string } | null)?.serial ?? null,
    report: (rep as IncidentReportRow | null) ?? null,
    clipUrl,
    clipDurationS,
    trace,
    traceState,
  };
}

async function objectExists(url: string): Promise<boolean> {
  try {
    const res = await fetch(url, { method: "HEAD", cache: "no-store" });
    return res.ok;
  } catch {
    return false;
  }
}
