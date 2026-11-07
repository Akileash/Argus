import type { ArgusEvent, ArgusReport } from "@argus/shared";

export interface MediaRef {
  /** Path inside the event-media bucket. */
  path: string;
  contentType: string;
}

/** Everything the pipeline needs about one event, resolved from the DB (never from the LLM). */
export interface EventContext {
  eventId: string;
  event: ArgusEvent;
  siteName: string;
  workerName: string;
  crewName: string;
  supervisorName: string;
  deviceSerial: string;
  clip: MediaRef | null;
  trace: MediaRef | null;
}

export interface AnalysisRow {
  event_id: string;
  stage: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  latency_ms: number;
  output_json: unknown;
}

export interface LlmSuggestion {
  suggested_class: "near_miss" | "incident" | null;
  reason: string | null;
}

/** Small DB/Storage boundary so the pipeline is testable without Supabase. */
export interface Store {
  /** Atomically move one `uploaded` event to `analyzing`; null when nothing is waiting. */
  claimNext(): Promise<string | null>;
  /** Re-queue events stuck in `analyzing` for longer than maxAgeMs (crashed worker). Returns their ids. */
  reclaimStale(maxAgeMs: number): Promise<string[]>;
  loadContext(eventId: string): Promise<EventContext>;
  /** Download an object from the event-media bucket. */
  download(path: string): Promise<Uint8Array>;
  /** Sum of analyses.cost_usd across all events (spend guard). */
  totalCostUsd(): Promise<number>;
  insertAnalysis(row: AnalysisRow): Promise<void>;
  /** Existing report_no for the event, else a fresh ARG-YYYY-NNNN. */
  reportNoFor(eventId: string, year: number): Promise<string>;
  /** Insert the event's report (draft == working copy). No-op if one exists: llm_draft_json is immutable. */
  saveReport(eventId: string, report: ArgusReport): Promise<void>;
  markReady(eventId: string, suggestion: LlmSuggestion, severity: number): Promise<void>;
  markFailed(eventId: string, error: string): Promise<void>;
}
