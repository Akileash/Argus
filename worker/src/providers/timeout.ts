import { LlmTimeoutError } from "../errors";

/**
 * Run `fn` with a hard time limit. `fn` receives an AbortSignal that fires at the limit, so SDK calls that accept
 * one are really cancelled; the race also settles when a call ignores the signal, so a hung request cannot stall the worker.
 */
export async function withDeadline<T>(ms: number, label: string, fn: (signal: AbortSignal) => Promise<T>): Promise<T> {
  const ac = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const limit = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      const err = new LlmTimeoutError(`${label} timed out after ${Math.round(ms / 1000)} s`);
      ac.abort(err);
      reject(err);
    }, ms);
  });
  try {
    const work = fn(ac.signal);
    work.catch(() => undefined); // if the limit wins, a late rejection from the abandoned call must not be unhandled
    return await Promise.race([work, limit]);
  } finally {
    clearTimeout(timer);
  }
}
