import type { ArgusEvent, ArgusReport } from "@argus/shared";

export interface MediaRef {
  /** Path inside the event-media bucket. */
  path: string;
  contentType: string;
  /** media.bytes as declared by the device and verified at /complete; null when unknown. */
  bytes?: number | null;
  /** media.confirmed: the upload was verified. An unconfirmed row may point at nothing. */
  confirmed?: boolean;
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
  /**
   * Atomically move one `uploaded` event to `analyzing` and count the claim (events.analysis_attempts + 1).
   * An event that already used `maxClaims` claims is marked failed instead of being returned. Null when nothing is waiting.
   */
  claimNext(maxClaims: number): Promise<string | null>;
  /**
   * Re-queue events stuck in `analyzing` for longer than maxAgeMs (crashed worker) that still have claims left.
   * Compare-and-set per event, so a worker that is alive (fresh updated_at) is never touched. Returns their ids.
   */
  reclaimStale(maxAgeMs: number, maxClaims?: number): Promise<string[]>;
  /** Mark failed the stale `analyzing` events that already used all `maxClaims` claims. Returns their ids. */
  failExhausted(maxAgeMs: number, maxClaims: number): Promise<string[]>;
  /** How many events are waiting (status `uploaded`). Used by the hosted route to report `remaining`. */
  countWaiting(): Promise<number>;
  /** Heartbeat: refresh updated_at of an event this worker is still working on. */
  touch(eventId: string): Promise<void>;
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

/** events.error text for an event that used all its claims. */
export function giveUpMessage(attempts: number, why: string): string {
  return `Gave up after ${attempts} analysis attempt${attempts === 1 ? "" : "s"}: ${why} each time and no report was produced. Check the worker logs; set the event back to uploaded with analysis_attempts = 0 to try again.`;
}
