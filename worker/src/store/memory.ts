import type { ArgusReport } from "@argus/shared";
import type { AnalysisRow, EventContext, LlmSuggestion, Store } from "./types";

export interface MemoryEvent {
  ctx: EventContext;
  status: string;
  error: string | null;
  llm_suggested_class: string | null;
  llm_reclass_reason: string | null;
  severity: number | null;
}

export interface MemoryReport {
  event_id: string;
  report_no: string;
  llm_draft_json: ArgusReport;
  report_json: ArgusReport;
  edited: boolean;
  status: "draft" | "signed";
}

/** In-memory Store for tests and offline demos. */
export class MemoryStore implements Store {
  events = new Map<string, MemoryEvent>();
  files = new Map<string, Uint8Array>();
  analyses: AnalysisRow[] = [];
  reports = new Map<string, MemoryReport>();
  /** Test hook: pre-existing spend from other events. */
  baseCostUsd = 0;
  /** Test hook: make the next N download() calls fail. */
  failDownloads = 0;

  addEvent(ctx: EventContext, status = "uploaded"): void {
    this.events.set(ctx.eventId, {
      ctx,
      status,
      error: null,
      llm_suggested_class: null,
      llm_reclass_reason: null,
      severity: null,
    });
  }

  async claimNext(): Promise<string | null> {
    for (const [id, ev] of this.events) {
      if (ev.status === "uploaded") {
        ev.status = "analyzing";
        return id;
      }
    }
    return null;
  }

  private get(eventId: string): MemoryEvent {
    const ev = this.events.get(eventId);
    if (!ev) throw new Error(`event ${eventId} not found`);
    return ev;
  }

  async loadContext(eventId: string): Promise<EventContext> {
    return this.get(eventId).ctx;
  }

  async download(path: string): Promise<Uint8Array> {
    if (this.failDownloads > 0) {
      this.failDownloads--;
      throw new Error("simulated storage outage");
    }
    const f = this.files.get(path);
    if (!f) throw new Error(`object not found: ${path}`);
    return f;
  }

  async totalCostUsd(): Promise<number> {
    return this.baseCostUsd + this.analyses.reduce((s, a) => s + a.cost_usd, 0);
  }

  async insertAnalysis(row: AnalysisRow): Promise<void> {
    this.analyses.push(row);
  }

  async reportNoFor(eventId: string, year: number): Promise<string> {
    const existing = this.reports.get(eventId);
    if (existing) return existing.report_no;
    const n = this.reports.size + 1;
    return `ARG-${year}-${String(n).padStart(4, "0")}`;
  }

  async saveReport(eventId: string, report: ArgusReport): Promise<void> {
    if (this.reports.has(eventId)) return; // llm_draft_json is immutable; keep the first draft
    this.reports.set(eventId, {
      event_id: eventId,
      report_no: report.header.report_no,
      llm_draft_json: structuredClone(report),
      report_json: structuredClone(report),
      edited: false,
      status: "draft",
    });
  }

  async markReady(eventId: string, s: LlmSuggestion, severity: number): Promise<void> {
    const ev = this.get(eventId);
    ev.status = "ready";
    ev.error = null;
    ev.llm_suggested_class = s.suggested_class;
    ev.llm_reclass_reason = s.reason;
    ev.severity = severity;
  }

  async markFailed(eventId: string, error: string): Promise<void> {
    const ev = this.get(eventId);
    ev.status = "failed";
    ev.error = error;
  }
}
