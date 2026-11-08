import { LlmTimeoutError, NonRetryableError } from "../errors";

/** Gemini stayed overloaded / unreachable through every retry. Not an event failure: the pipeline falls back to sensor data. */
export class TransientExhaustedError extends Error {
  constructor(message: string, readonly tries: number) {
    super(message);
  }
}

const TRANSIENT_HTTP = new Set([408, 429, 500, 502, 503, 504]);
const TRANSIENT_STATUS = new Set(["UNAVAILABLE", "RESOURCE_EXHAUSTED", "INTERNAL", "DEADLINE_EXCEEDED"]);
const TRANSIENT_NET_CODES = new Set(["ECONNRESET", "ECONNREFUSED", "ETIMEDOUT", "EAI_AGAIN", "ENOTFOUND", "EPIPE", "ECONNABORTED", "UND_ERR_SOCKET", "UND_ERR_CONNECT_TIMEOUT"]);

/**
 * @google/genai throws ApiError (message = the response body as JSON text, `status` = the HTTP code as a number).
 * The Google error envelope {error:{code,status,message}} sits inside that message; network failures are TypeError
 * ("fetch failed") with the OS error in `cause.code`. Structured fields first, the message text is a last resort.
 */
export function isTransientError(e: unknown): boolean {
  if (e instanceof LlmTimeoutError || e instanceof NonRetryableError) return false;
  if (!e || typeof e !== "object") return false;
  const err = e as { status?: unknown; code?: unknown; cause?: unknown; message?: unknown; name?: string };
  const http = typeof err.status === "number" ? err.status : typeof err.code === "number" ? err.code : undefined;
  if (http !== undefined) return TRANSIENT_HTTP.has(http);
  if (typeof err.status === "string" && TRANSIENT_STATUS.has(err.status)) return true;
  if (typeof err.code === "string" && TRANSIENT_NET_CODES.has(err.code)) return true;
  const cause = err.cause as { code?: unknown } | undefined;
  if (cause && typeof cause.code === "string" && TRANSIENT_NET_CODES.has(cause.code)) return true;
  const msg = typeof err.message === "string" ? err.message : "";
  // ApiError.message is the JSON body; read its envelope rather than grepping the whole string.
  if (msg.startsWith("{")) {
    try {
      const env = (JSON.parse(msg) as { error?: { code?: unknown; status?: unknown } }).error;
      if (env) {
        if (typeof env.code === "number" && TRANSIENT_HTTP.has(env.code)) return true;
        if (typeof env.status === "string" && TRANSIENT_STATUS.has(env.status)) return true;
      }
    } catch {
      /* not JSON */
    }
  }
  if (err.name === "TypeError" && /fetch failed|network|socket|terminated/i.test(msg)) return true;
  return /high demand|UNAVAILABLE|RESOURCE_EXHAUSTED/.test(msg);
}

/** Short human reason for confidence_and_gaps. */
export function describeTransient(e: unknown): string {
  const msg = e instanceof Error ? e.message : String(e);
  const status = (e as { status?: unknown } | null)?.status;
  if (/high demand/i.test(msg)) return "high demand";
  if (/RESOURCE_EXHAUSTED/.test(msg) || status === 429 || /\b429\b/.test(msg)) return "rate limited";
  if (/UNAVAILABLE/.test(msg) || status === 503 || /\b503\b/.test(msg)) return "service unavailable";
  if (/fetch failed|ECONN|ENOTFOUND|network/i.test(msg)) return "network error";
  return "server error";
}

export interface TransientOpts {
  /** Total tries including the first. 1 disables retrying. */
  tries: number;
  /** Delay before the 2nd try; doubles each time, plus up to 25% jitter. */
  baseMs: number;
  sleep: (ms: number) => Promise<void>;
  random?: () => number;
  onRetry?: (info: { attempt: number; tries: number; delayMs: number; error: unknown }) => void;
}

/** Longest total backoff sleep for these options (jitter included). */
export function maxBackoffMs(tries: number, baseMs: number): number {
  let s = 0;
  for (let i = 1; i < tries; i++) s += baseMs * 2 ** (i - 1) * 1.25;
  return s;
}

/**
 * Retry `fn` on transient errors only. Each try carries its own time limit inside `fn`; a timeout is never retried.
 * Throws TransientExhaustedError once the tries are used up; any other error passes through untouched.
 */
export async function withTransientRetry<T>(opts: TransientOpts, fn: () => Promise<T>): Promise<T> {
  const rnd = opts.random ?? Math.random;
  const tries = Math.max(1, Math.floor(opts.tries));
  for (let n = 1; ; n++) {
    try {
      return await fn();
    } catch (e) {
      if (!isTransientError(e)) throw e;
      if (n >= tries) {
        throw new TransientExhaustedError(`Gemini was unavailable (${describeTransient(e)}) after ${n} ${n === 1 ? "try" : "tries"}`, n);
      }
      const delayMs = Math.round(opts.baseMs * 2 ** (n - 1) * (1 + 0.25 * rnd()));
      opts.onRetry?.({ attempt: n, tries, delayMs, error: e });
      await opts.sleep(delayMs);
    }
  }
}
