import { after } from "next/server";
import { createWorkerDeps } from "./deps";
import { drainEvents } from "./process";

/** WORKER_INLINE=off disables the in-app worker (use it when you run the standalone `@argus/worker start` process). */
export function inlineWorkerEnabled(env: NodeJS.ProcessEnv = process.env): boolean {
  return (env.WORKER_INLINE ?? "on").trim().toLowerCase() !== "off";
}

/**
 * Start report processing after the response has been sent. The device never waits for it, and nothing in here
 * can fail its request: errors are logged and the cron safety net picks the event up.
 */
export function scheduleReportProcessing(): void {
  if (!inlineWorkerEnabled()) return;
  try {
    after(async () => {
      try {
        await drainEvents(createWorkerDeps(), { log: (m) => console.log(`[worker] ${m}`) });
      } catch (e) {
        console.error(`[worker] background run failed: ${(e as Error).message}`);
      }
    });
  } catch (e) {
    console.error(`[worker] could not schedule background run: ${(e as Error).message}`);
  }
}
