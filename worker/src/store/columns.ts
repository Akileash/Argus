/**
 * ALL table and column names the worker touches live in this one file. Aligned with
 * supabase/migrations/20260101000000_argus_schema.sql (branch paradev-workspace-setup-54999331).
 * org_id on media/analyses/reports is filled by DB triggers, so the worker never sends it.
 */

export const TABLES = {
  events: "events",
  devices: "devices",
  workers: "workers",
  crews: "crews",
  sites: "sites",
  media: "media",
  analyses: "analyses",
  reports: "reports",
} as const;

export const EVENT_COLS = {
  id: "id",
  status: "status",
  updatedAt: "updated_at",
  error: "error",
  deviceId: "device_id",
  workerId: "worker_id",
  siteId: "site_id",
  type: "type",
  /** Risk score (1-25); the worker sets it to report.risk.score when the report is ready. */
  severity: "severity",
  t0: "t0",
  detectedAt: "detected_at",
  windowPreS: "window_pre_s",
  windowPostS: "window_post_s",
  deviceClass: "device_class",
  llmSuggestedClass: "llm_suggested_class",
  llmReclassReason: "llm_reclass_reason",
  peakG: "peak_g",
  peakGSaturated: "peak_g_saturated",
  closingRateMps: "closing_rate_mps",
  ttcS: "ttc_s",
  rangeM: "range_m",
  sensorId: "sensor_id",
  freeFallMs: "free_fall_ms",
  dropHeightM: "drop_height_m",
  hatOn: "hat_on",
  co2Ppm: "co2_ppm",
  tempC: "temp_c",
  rhPct: "rh_pct",
  mergedTriggers: "merged_triggers",
  firmware: "firmware",
  /** Raw ArgusEvent body as received (preferred source); scalar columns are the fallback. */
  payload: "payload",
} as const;

export const DEVICE_COLS = { id: "id", serial: "serial" } as const;
export const WORKER_COLS = { id: "id", fullName: "full_name", crewId: "crew_id" } as const;
export const CREW_COLS = { id: "id", name: "name", supervisorName: "supervisor_name" } as const;
export const SITE_COLS = { id: "id", name: "name" } as const;

export const MEDIA_COLS = {
  eventId: "event_id",
  kind: "kind",
  storagePath: "storage_path",
  filename: "filename",
  camera: "camera",
} as const;

export const ANALYSIS_COLS = {
  id: "id",
  eventId: "event_id",
  stage: "stage",
  model: "model",
  inputTokens: "input_tokens",
  outputTokens: "output_tokens",
  costUsd: "cost_usd",
  latencyMs: "latency_ms",
  outputJson: "output_json",
} as const;

export const REPORT_COLS = {
  id: "id",
  eventId: "event_id",
  llmDraftJson: "llm_draft_json",
  reportJson: "report_json",
  edited: "edited",
  status: "status",
  reportNo: "report_no",
} as const;

/** Postgres function allocating ARG-YYYY-NNNN (called via rpc with the service role). */
export const RPC_NEXT_REPORT_NO = "next_report_no";
