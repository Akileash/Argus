import type { SupabaseClient } from "@supabase/supabase-js";
import type { EventClass } from "@/components/chips";
import { DISPLAY_TZ } from "@/lib/report/format";

export type EventStatus = "received" | "uploading" | "uploaded" | "analyzing" | "ready" | "failed";

/** Select string for one dashboard list row. Never selects devices.token_hash. */
export const EVENT_ROW_SELECT =
  "id, type, t0, device_class, final_class, llm_suggested_class, severity, status, " +
  "device:devices(serial), worker:workers(full_name, crew:crews(name)), report:reports(report_no, status)";

export interface IncidentRow {
  id: string;
  type: string;
  typeLabel: string;
  t0: string;
  cls: EventClass;
  deviceClass: EventClass;
  llmSuggestedClass: EventClass | null;
  severity: number | null;
  status: EventStatus;
  device: string | null;
  worker: string | null;
  crew: string | null;
  reportNo: string | null;
  reportStatus: "draft" | "signed" | null;
  /** Number shown in the list: report number once drafted, else short event id. */
  ref: string;
  needsSignoff: boolean;
  aiSuggestsReclass: boolean;
  quiet: boolean;
}

export interface Kpis {
  nearMissesWeek: number;
  incidentsWeek: number;
  awaitingSignoff: number;
}

export interface DeviceSeen {
  deviceId: string;
  serial: string;
  lastEventAt: string | null;
  workerName: string | null;
}

export const TYPE_LABELS: Record<string, string> = {
  struck_by_near_miss: "Struck-by near miss",
  struck_by_contact: "Struck-by contact",
  head_impact: "Head impact",
  fall: "Fall",
  hat_knocked_off: "Hat knocked off",
  hat_removed: "Hat removed",
  module_fallback: "Module fallback",
};

export function typeLabel(type: string): string {
  return TYPE_LABELS[type] ?? type.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

/** PostgREST embeds come back as an object or a one-element array depending on inferred cardinality. */
function one<T>(v: T | T[] | null | undefined): T | null {
  if (Array.isArray(v)) return v[0] ?? null;
  return v ?? null;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function toIncidentRow(raw: any): IncidentRow {
  const device = one<{ serial: string }>(raw.device);
  const worker = one<{ full_name: string; crew: { name: string } | { name: string }[] | null }>(raw.worker);
  const crew = one<{ name: string }>(worker?.crew);
  const report = one<{ report_no: string; status: "draft" | "signed" }>(raw.report);
  const deviceClass = raw.device_class as EventClass;
  const cls = (raw.final_class ?? deviceClass) as EventClass;
  const llm = (raw.llm_suggested_class ?? null) as EventClass | null;
  return {
    id: raw.id,
    type: raw.type,
    typeLabel: typeLabel(raw.type),
    t0: raw.t0,
    cls,
    deviceClass,
    llmSuggestedClass: llm,
    severity: raw.severity ?? null,
    status: raw.status,
    device: device?.serial ?? null,
    worker: worker?.full_name ?? null,
    crew: crew?.name ?? null,
    reportNo: report?.report_no ?? null,
    reportStatus: report?.status ?? null,
    ref: report?.report_no ?? `EVT-${String(raw.id).slice(-4)}`,
    needsSignoff: report?.status === "draft",
    aiSuggestsReclass: llm !== null && llm !== cls && report?.status !== "signed",
    quiet: raw.type === "hat_removed" || cls === "ppe_log",
  };
}

/** Newest first by t0; stable on id. */
export function sortRows(rows: IncidentRow[]): IncidentRow[] {
  return [...rows].sort((a, b) => {
    const ta = new Date(a.t0).getTime();
    const tb = new Date(b.t0).getTime();
    if (ta !== tb) return tb - ta;
    return a.id < b.id ? 1 : -1;
  });
}

export function upsertRow(rows: IncidentRow[], row: IncidentRow): IncidentRow[] {
  const i = rows.findIndex((r) => r.id === row.id);
  if (i === -1) return sortRows([row, ...rows]);
  const next = [...rows];
  next[i] = row;
  return sortRows(next);
}

export type ClassFilter = "all" | "near_miss" | "incident" | "ppe_log";

export function filterRows(rows: IncidentRow[], cls: ClassFilter, needsSignoffOnly: boolean): IncidentRow[] {
  return rows.filter((r) => (cls === "all" || r.cls === cls) && (!needsSignoffOnly || r.needsSignoff));
}

/** Compact relative time: "just now", "4 min ago", "3 h ago", "2 d ago". */
export function formatRelative(iso: string | null, now: number): string {
  if (!iso) return "never";
  const diff = Math.max(0, now - new Date(iso).getTime());
  const s = Math.floor(diff / 1000);
  if (s < 45) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${Math.max(m, 1)} min ago`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.floor(h / 24)} d ago`;
}

const dtf = new Intl.DateTimeFormat("en-CA", {
  timeZone: DISPLAY_TZ,
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});
export function formatEventTime(iso: string): string {
  return dtf.format(new Date(iso));
}

export function toKpis(
  raw: { near_misses_week?: number | null; incidents_week?: number | null; reports_awaiting_signoff?: number | null } | null,
): Kpis {
  return {
    nearMissesWeek: Number(raw?.near_misses_week ?? 0),
    incidentsWeek: Number(raw?.incidents_week ?? 0),
    awaitingSignoff: Number(raw?.reports_awaiting_signoff ?? 0),
  };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function toDeviceSeen(raw: any): DeviceSeen {
  return {
    deviceId: raw.device_id,
    serial: raw.serial,
    lastEventAt: raw.last_event_at ?? null,
    workerName: raw.assigned_worker_name ?? null,
  };
}

/** Most recently seen first, never-seen last. */
export function sortDevices(d: DeviceSeen[]): DeviceSeen[] {
  return [...d].sort((a, b) => {
    if (!a.lastEventAt && !b.lastEventAt) return a.serial.localeCompare(b.serial);
    if (!a.lastEventAt) return 1;
    if (!b.lastEventAt) return -1;
    return a.lastEventAt < b.lastEventAt ? 1 : -1;
  });
}

export interface HomeData {
  rows: IncidentRow[];
  kpis: Kpis;
  devices: DeviceSeen[];
}

export async function fetchRows(supabase: SupabaseClient, limit = 100): Promise<IncidentRow[]> {
  const { data, error } = await supabase.from("events").select(EVENT_ROW_SELECT).order("t0", { ascending: false }).limit(limit);
  if (error) throw new Error(error.message);
  return (data ?? []).map(toIncidentRow);
}

export async function fetchRow(supabase: SupabaseClient, id: string): Promise<IncidentRow | null> {
  const { data, error } = await supabase.from("events").select(EVENT_ROW_SELECT).eq("id", id).maybeSingle();
  if (error) throw new Error(error.message);
  return data ? toIncidentRow(data) : null;
}

export async function fetchKpis(supabase: SupabaseClient): Promise<Kpis> {
  const { data, error } = await supabase
    .from("org_kpis_week")
    .select("near_misses_week, incidents_week, reports_awaiting_signoff")
    .maybeSingle();
  if (error) throw new Error(error.message);
  return toKpis(data);
}

export async function fetchDevices(supabase: SupabaseClient): Promise<DeviceSeen[]> {
  const { data, error } = await supabase.from("device_last_seen").select("device_id, serial, last_event_at, assigned_worker_name");
  if (error) throw new Error(error.message);
  return sortDevices((data ?? []).map(toDeviceSeen));
}

export async function fetchHomeData(supabase: SupabaseClient): Promise<HomeData> {
  const [rows, kpis, devices] = await Promise.all([fetchRows(supabase), fetchKpis(supabase), fetchDevices(supabase)]);
  return { rows, kpis, devices };
}

/** Toast headline for a freshly inserted event: "Struck-by near miss — ARG-0001 / J. Smith". */
export function toastTitle(row: IncidentRow): string {
  const who = [row.device, row.worker].filter(Boolean).join(" / ");
  return `${row.typeLabel} — ${who || row.ref}`;
}
