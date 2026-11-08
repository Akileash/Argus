// Validates LLM drafts with the worker's real assembleReport (zod + contracts/report.schema.json).
// Usage (from worker/): pnpm exec tsx ../scripts/llm-bench/validate.ts <draft.json>...   -> JSON lines {file, valid, error}
// The case is taken from the file name (contains "__fall__" or "__struck_by__").
import { readFileSync } from "node:fs";
import { assembleReport } from "../../worker/src/pipeline/assemble";

const ctxFor = (t: string): any => ({
  eventId: "x",
  event: { device_class: t === "fall" ? "incident" : "near_miss", t0: "2026-06-12T15:42:07.000Z" },
  siteName: "Example Project - Edmonton",
  workerName: "J. Smith",
  crewName: "Framing A",
  supervisorName: "A. Tremblay",
  deviceSerial: "ARGUS-001",
});
for (const f of process.argv.slice(2)) {
  const caseName = f.includes("__fall__") ? "fall" : "struck_by";
  try {
    const j = JSON.parse(readFileSync(f, "utf8"));
    assembleReport(j, ctxFor(caseName), "ARG-2026-0001");
    console.log(JSON.stringify({ file: f, valid: true }));
  } catch (e) {
    console.log(JSON.stringify({ file: f, valid: false, error: (e as Error).message.slice(0, 400) }));
  }
}
