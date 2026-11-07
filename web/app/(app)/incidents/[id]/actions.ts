"use server";

import { createClient } from "@/lib/supabase/server";
import { normalizeReport, validateReport, type ReportJson } from "@/lib/report/model";

export type ActionResult<T = object> = ({ ok: true } & T) | { ok: false; error: string; locked?: boolean };

const LOCKED_MSG = "This report is already signed (or you no longer have permission to edit it). Reload to see the signed version.";

/** Postgres raises 55006 when a report is signed/locked (supabase/README.md). RLS hides signed rows from UPDATE, so 0 rows also means locked. */
function isLocked(err: { code?: string; message?: string } | null): boolean {
  return !!err && (err.code === "55006" || /signed and locked|immutable/i.test(err.message ?? ""));
}

/** Save manager edits to report_json (RLS: manager + draft; trigger logs report_edits and sets `edited`). */
export async function saveReport(reportId: string, report: unknown): Promise<ActionResult<{ report: ReportJson; edited: boolean; updatedAt: string }>> {
  const v = validateReport(report);
  if (!v.ok) return { ok: false, error: v.error };
  const next = normalizeReport(v.report);
  // sign_off is only ever written by the sign-off action
  next.sign_off = { signed_by: null, signed_at: null, final_class: null, reclass_decision: null };

  const supabase = await createClient();
  const { data, error } = await supabase
    .from("reports")
    .update({ report_json: next })
    .eq("id", reportId)
    .eq("status", "draft")
    .select("report_json, edited, updated_at");
  if (error) return isLocked(error) ? { ok: false, error: LOCKED_MSG, locked: true } : { ok: false, error: error.message };
  if (!data || data.length === 0) return { ok: false, error: LOCKED_MSG, locked: true };
  return { ok: true, report: data[0].report_json as ReportJson, edited: data[0].edited, updatedAt: data[0].updated_at };
}

export type ReclassDecision = "accepted" | "rejected";

/**
 * Sign off: persist the manager's last draft edits to report_json, then call the sign_off_report() RPC, which in ONE
 * transaction checks the caller is a manager of the report's org, takes the signer name from their profile, derives the
 * event from the report, validates the reclass decision and writes sign_off, status and events.final_class.
 * Clients cannot write those columns directly (supabase/migrations/*_sign_off_report.sql).
 */
export async function signOffReport(input: {
  reportId: string;
  report: unknown;
  reclassDecision: ReclassDecision | null;
}): Promise<ActionResult<{ signedBy: string; signedAt: string; finalClass: string }>> {
  const v = validateReport(input.report);
  if (!v.ok) return { ok: false, error: v.error };
  if (input.reclassDecision !== null && input.reclassDecision !== "accepted" && input.reclassDecision !== "rejected") {
    return { ok: false, error: "Invalid reclassification decision." };
  }

  const supabase = await createClient();
  const { data: auth } = await supabase.auth.getUser();
  if (!auth.user) return { ok: false, error: "Your session expired. Sign in again." };

  const report = normalizeReport(v.report);
  report.sign_off = { signed_by: null, signed_at: null, final_class: null, reclass_decision: null };
  const { data: saved, error: saveErr } = await supabase
    .from("reports")
    .update({ report_json: report })
    .eq("id", input.reportId)
    .eq("status", "draft")
    .select("id");
  if (saveErr) return isLocked(saveErr) ? { ok: false, error: LOCKED_MSG, locked: true } : { ok: false, error: saveErr.message };
  if (!saved || saved.length === 0) return { ok: false, error: LOCKED_MSG, locked: true };

  const { data, error } = await supabase.rpc("sign_off_report", {
    p_report_id: input.reportId,
    p_reclass_decision: input.reclassDecision,
  });
  if (error) {
    if (isLocked(error)) return { ok: false, error: LOCKED_MSG, locked: true };
    if (error.code === "42501") return { ok: false, error: "Only managers of this organization can sign off reports." };
    return { ok: false, error: error.message };
  }
  const r = data as { signed_by: string; signed_at: string; final_class: string };
  return { ok: true, signedBy: r.signed_by, signedAt: r.signed_at, finalClass: r.final_class };
}
