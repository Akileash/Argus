import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { EVENT_TYPE_TO_CLASS, STORAGE_BUCKET, type ArgusEvent, type ArgusReport, type EventType } from "@argus/shared";
import {
  ANALYSIS_COLS as A,
  CREW_COLS as C,
  DEVICE_COLS as D,
  EVENT_COLS as E,
  MEDIA_COLS as M,
  REPORT_COLS as R,
  SITE_COLS as S,
  RPC_NEXT_REPORT_NO,
  TABLES as T,
  WORKER_COLS as W,
} from "./columns";
import { giveUpMessage, type AnalysisRow, type EventContext, type LlmSuggestion, type MediaRef, type Store } from "./types";

type Row = Record<string, any>;

function must<T>(res: { data: T | null; error: { message: string } | null }, what: string): T {
  if (res.error) throw new Error(`${what}: ${res.error.message}`);
  if (res.data === null) throw new Error(`${what}: no data`);
  return res.data;
}

export class SupabaseStore implements Store {
  readonly db: SupabaseClient;

  constructor(url: string, serviceRoleKey: string) {
    this.db = createClient(url, serviceRoleKey, { auth: { persistSession: false, autoRefreshToken: false } });
  }

  async claimNext(maxClaims: number): Promise<string | null> {
    // A few candidates at most: exhausted ones are failed on the way and skipped.
    for (let i = 0; i < 10; i++) {
      const q = await this.db
        .from(T.events)
        .select(`${E.id}, ${E.analysisAttempts}`)
        .eq(E.status, "uploaded")
        .order(E.t0, { ascending: true })
        .limit(1);
      const rows = must(q, "list uploaded events") as unknown as Row[];
      const id = rows[0]?.[E.id] as string | undefined;
      if (!id) return null;
      const attempts = Number(rows[0]![E.analysisAttempts] ?? 0);
      if (attempts >= maxClaims) {
        const f = await this.db
          .from(T.events)
          .update({ [E.status]: "failed", [E.error]: giveUpMessage(attempts, "it was queued again") })
          .eq(E.id, id)
          .eq(E.status, "uploaded")
          .eq(E.analysisAttempts, attempts);
        if (f.error) throw new Error(`fail exhausted event: ${f.error.message}`);
        continue;
      }
      // Compare-and-set: only one worker flips uploaded -> analyzing (and bumps the counter from the value it read).
      const upd = await this.db
        .from(T.events)
        .update({ [E.status]: "analyzing", [E.error]: null, [E.analysisAttempts]: attempts + 1 })
        .eq(E.id, id)
        .eq(E.status, "uploaded")
        .eq(E.analysisAttempts, attempts)
        .select(E.id);
      const won = must(upd, "claim event") as unknown as Row[];
      if (won.length === 1) return id;
      // Lost the race: another worker took it. Report "nothing for me" and let the poll loop come back.
      return null;
    }
    return null;
  }

  async reclaimStale(maxAgeMs: number, maxClaims = Number.MAX_SAFE_INTEGER): Promise<string[]> {
    const cutoff = new Date(Date.now() - maxAgeMs).toISOString();
    // One conditional UPDATE: an event whose worker refreshed updated_at after the cutoff does not match.
    const res = await this.db
      .from(T.events)
      .update({ [E.status]: "uploaded" })
      .eq(E.status, "analyzing")
      .lt(E.updatedAt, cutoff)
      .lt(E.analysisAttempts, maxClaims)
      .select(E.id);
    const rows = must(res, "reclaim stale events") as unknown as Row[];
    return rows.map((r) => r[E.id] as string);
  }

  async failExhausted(maxAgeMs: number, maxClaims: number): Promise<string[]> {
    const cutoff = new Date(Date.now() - maxAgeMs).toISOString();
    const res = await this.db
      .from(T.events)
      .update({
        [E.status]: "failed",
        [E.error]: giveUpMessage(maxClaims, "the worker stopped responding"),
      })
      .eq(E.status, "analyzing")
      .lt(E.updatedAt, cutoff)
      .gte(E.analysisAttempts, maxClaims)
      .select(E.id);
    const rows = must(res, "fail exhausted events") as unknown as Row[];
    return rows.map((r) => r[E.id] as string);
  }

  async countWaiting(): Promise<number> {
    const res = await this.db.from(T.events).select(E.id, { count: "exact", head: true }).eq(E.status, "uploaded");
    if (res.error) throw new Error(`count waiting events: ${res.error.message}`);
    return res.count ?? 0;
  }

  async touch(eventId: string): Promise<void> {
    // Any UPDATE fires the events_updated_at trigger, which sets updated_at = now().
    const res = await this.db.from(T.events).update({ [E.status]: "analyzing" }).eq(E.id, eventId).eq(E.status, "analyzing");
    if (res.error) throw new Error(`heartbeat: ${res.error.message}`);
  }

  async loadContext(eventId: string): Promise<EventContext> {
    const evRow = must(
      await this.db.from(T.events).select("*").eq(E.id, eventId).single(),
      "load event",
    ) as Row;

    const [device, worker, site, media] = await Promise.all([
      evRow[E.deviceId]
        ? this.db.from(T.devices).select("*").eq(D.id, evRow[E.deviceId]).maybeSingle()
        : Promise.resolve({ data: null, error: null }),
      evRow[E.workerId]
        ? this.db.from(T.workers).select("*").eq(W.id, evRow[E.workerId]).maybeSingle()
        : Promise.resolve({ data: null, error: null }),
      evRow[E.siteId]
        ? this.db.from(T.sites).select("*").eq(S.id, evRow[E.siteId]).maybeSingle()
        : Promise.resolve({ data: null, error: null }),
      this.db.from(T.media).select("*").eq(M.eventId, eventId),
    ]);
    const workerRow = (worker.data ?? null) as Row | null;
    const crew =
      workerRow?.[W.crewId]
        ? ((await this.db.from(T.crews).select("*").eq(C.id, workerRow[W.crewId]).maybeSingle()).data as Row | null)
        : null;
    const mediaRows = must(media, "load media") as unknown as Row[];
    const pick = (kind: string): MediaRef | null => {
      const r = mediaRows.find((m) => m[M.kind] === kind);
      if (!r) return null;
      // bytes and confirmed are carried through: the pipeline decides whether a clip is usable (run.ts clipGap).
      return {
        path: r[M.storagePath] as string,
        contentType: kind === "clip" ? "video/mp4" : "application/json",
        bytes: r[M.bytes] === null || r[M.bytes] === undefined ? null : Number(r[M.bytes]),
        confirmed: r[M.confirmed] === true,
      };
    };

    return {
      eventId,
      event: rebuildEvent(evRow),
      siteName: ((site.data as Row | null)?.[S.name] as string) ?? "Unknown site",
      workerName: (workerRow?.[W.fullName] as string) ?? "Unassigned worker",
      crewName: (crew?.[C.name] as string) ?? "Unassigned crew",
      supervisorName: (crew?.[C.supervisorName] as string) ?? "Unknown supervisor",
      deviceSerial: ((device.data as Row | null)?.[D.serial] as string) ?? String(evRow[E.deviceId] ?? "unknown"),
      clip: pick("clip"),
      trace: pick("sensor_trace"),
    };
  }

  async download(path: string): Promise<Uint8Array> {
    const { data, error } = await this.db.storage.from(STORAGE_BUCKET).download(path);
    if (error || !data) throw new Error(`download ${path}: ${error?.message ?? "empty"}`);
    return new Uint8Array(await data.arrayBuffer());
  }

  async totalCostUsd(): Promise<number> {
    let total = 0;
    const page = 1000;
    for (let from = 0; ; from += page) {
      const res = await this.db
        .from(T.analyses)
        .select(A.costUsd)
        .range(from, from + page - 1);
      const rows = must(res, "sum analyses") as unknown as Row[];
      for (const r of rows) total += Number(r[A.costUsd] ?? 0);
      if (rows.length < page) break;
    }
    return total;
  }

  async insertAnalysis(row: AnalysisRow): Promise<void> {
    const res = await this.db.from(T.analyses).insert({
      [A.eventId]: row.event_id,
      [A.stage]: row.stage,
      [A.model]: row.model,
      [A.inputTokens]: row.input_tokens,
      [A.outputTokens]: row.output_tokens,
      [A.costUsd]: row.cost_usd,
      [A.latencyMs]: row.latency_ms,
      [A.outputJson]: row.output_json,
    });
    if (res.error) throw new Error(`insert analysis: ${res.error.message}`);
  }

  async reportNoFor(eventId: string, _year: number): Promise<string> {
    const existing = await this.db.from(T.reports).select(R.reportNo).eq(R.eventId, eventId).maybeSingle();
    const prev = (existing.data as Row | null)?.[R.reportNo] as string | undefined;
    if (prev) return prev;
    const { data, error } = await this.db.rpc(RPC_NEXT_REPORT_NO);
    if (error || typeof data !== "string") throw new Error(`next_report_no: ${error?.message ?? "no value"}`);
    return data;
  }

  async saveReport(eventId: string, report: ArgusReport): Promise<void> {
    // reports.event_id is unique and llm_draft_json is immutable: if a previous attempt already
    // inserted the draft, keep it.
    const existing = await this.db.from(T.reports).select(R.id).eq(R.eventId, eventId).maybeSingle();
    if (existing.data) return;
    const res = await this.db.from(T.reports).insert({
      [R.eventId]: eventId,
      [R.reportNo]: report.header.report_no,
      [R.llmDraftJson]: report,
      [R.reportJson]: report,
      [R.edited]: false,
      [R.status]: "draft",
    });
    if (res.error) throw new Error(`save report: ${res.error.message}`);
  }

  async markReady(eventId: string, s: LlmSuggestion, severity: number): Promise<void> {
    const res = await this.db
      .from(T.events)
      .update({
        [E.status]: "ready",
        [E.error]: null,
        [E.llmSuggestedClass]: s.suggested_class,
        [E.llmReclassReason]: s.reason,
        [E.severity]: severity,
      })
      .eq(E.id, eventId)
      .eq(E.status, "analyzing");
    if (res.error) throw new Error(`mark ready: ${res.error.message}`);
  }

  async markFailed(eventId: string, error: string): Promise<void> {
    const res = await this.db
      .from(T.events)
      .update({ [E.status]: "failed", [E.error]: error.slice(0, 2000) })
      .eq(E.id, eventId)
      .eq(E.status, "analyzing"); // never overwrite an event someone else already finished or re-queued
    if (res.error) throw new Error(`mark failed: ${res.error.message}`);
  }
}

const num = (v: unknown): number | null => (v === null || v === undefined ? null : Number(v));

/** Prefer the stored device payload; otherwise rebuild a reduced ArgusEvent from scalar columns. */
export function rebuildEvent(row: Row): ArgusEvent {
  const payload = row[E.payload];
  if (payload && typeof payload === "object") return payload as ArgusEvent;
  const type = row[E.type] as EventType;
  const iso = (v: unknown) => new Date(v as string).toISOString();
  return {
    schema_version: "1",
    event_id: row[E.id],
    device_serial: String(row[E.deviceId] ?? ""),
    type,
    device_class: (row[E.deviceClass] ?? EVENT_TYPE_TO_CLASS[type]) as ArgusEvent["device_class"],
    t0: iso(row[E.t0]),
    detected_at: iso(row[E.detectedAt] ?? row[E.t0]),
    window: { pre_s: Number(row[E.windowPreS] ?? 20), post_s: Number(row[E.windowPostS] ?? 10) },
    metrics: {
      peak_g: num(row[E.peakG]),
      peak_g_saturated: Boolean(row[E.peakGSaturated]),
      closing_rate_mps: num(row[E.closingRateMps]),
      ttc_s: num(row[E.ttcS]),
      range_m: num(row[E.rangeM]),
      sensor_id: row[E.sensorId] ?? null,
      free_fall_ms: num(row[E.freeFallMs]),
      drop_height_m: num(row[E.dropHeightM]),
      hat_on: Boolean(row[E.hatOn]),
    },
    merged_triggers: Array.isArray(row[E.mergedTriggers]) ? row[E.mergedTriggers] : [],
    env: { co2_ppm: num(row[E.co2Ppm]), temp_c: num(row[E.tempC]), rh_pct: num(row[E.rhPct]) },
    media: [],
    firmware: { version: "unknown", impls: {} },
  };
}
