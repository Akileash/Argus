import { LOCATION_PLACEHOLDER, REPORT_FOOTER, type ArgusReport } from "@argus/shared";
import { LLM_KEYS, assertValidReport } from "../llm-schema";
import type { EventContext } from "../store/types";

/**
 * Build the final report. Only the LLM-owned analysis keys are taken from `llm`; everything the
 * worker owns is written here from the DB / constants and CANNOT be influenced by model output,
 * whatever it returned for header, location, classification, risk score, sign-off or footer.
 */
export function assembleReport(llm: unknown, ctx: EventContext, reportNo: string): ArgusReport {
  if (llm === null || typeof llm !== "object" || Array.isArray(llm)) {
    throw new Error("model output is not a JSON object");
  }
  const src = llm as Record<string, any>;
  const picked: Record<string, any> = {};
  for (const k of LLM_KEYS) picked[k] = src[k];

  const risk = picked.risk;
  if (risk === null || typeof risk !== "object") throw new Error("model output has no risk object");
  const severity = risk.severity;
  const likelihood = risk.likelihood;
  if (!Number.isInteger(severity) || !Number.isInteger(likelihood)) {
    throw new Error("risk.severity and risk.likelihood must be integers");
  }
  picked.risk = { ...risk, score: severity * likelihood };

  // Advisory only: normalise so "no disagreement" is always null/null, and ppe_log is never reclassified.
  const deviceClass = ctx.event.device_class;
  const review = picked.classification_review ?? { suggested_class: null, reason: null };
  const suggested = review.suggested_class ?? null;
  const agrees = suggested === null || suggested === deviceClass || deviceClass === "ppe_log";
  picked.classification_review = agrees
    ? { suggested_class: null, reason: null }
    : { suggested_class: suggested, reason: typeof review.reason === "string" && review.reason ? review.reason : null };

  if (deviceClass === "system") throw new Error("system events do not produce reports");

  const report = {
    schema_version: "1" as const,
    header: {
      report_no: reportNo,
      classification: deviceClass,
      occurred_at: ctx.event.t0,
      site: ctx.siteName,
      location_on_site: LOCATION_PLACEHOLDER,
      worker: ctx.workerName,
      crew: ctx.crewName,
      supervisor: ctx.supervisorName,
      device: ctx.deviceSerial,
    },
    ...picked,
    sign_off: { signed_by: null, signed_at: null, final_class: null, reclass_decision: null },
    footer: REPORT_FOOTER,
  };
  return assertValidReport(report);
}
