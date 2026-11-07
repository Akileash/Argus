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
 * Sign off: ONE reports UPDATE (status, signed_off_by, signed_off_at, report_json incl. sign_off), then the
 * separate events.final_class UPDATE. The signer name always comes from the caller's profile, never the client.
 */
export async function signOffReport(input: {
  reportId: string;
  eventId: string;
  report: unknown;
  reclassDecision: ReclassDecision | null;
}): Promise<ActionResult<{ signedBy: string; signedAt: string; finalClass: string }>> {
  const v = validateReport(input.report);
  if (!v.ok) return { ok: false, error: v.error };

  const supabase = await createClient();
  const { data: auth } = await supabase.auth.getUser();
  if (!auth.user) return { ok: false, error: "Your session expired. Sign in again." };

  const [{ data: profile }, { data: event }] = await Promise.all([
    supabase.from("profiles").select("full_name, role").eq("user_id", auth.user.id).maybeSingle(),
    supabase.from("events").select("device_class, llm_suggested_class").eq("id", input.eventId).maybeSingle(),
  ]);
  if (!profile || profile.role !== "manager") return { ok: false, error: "Only managers can sign off reports." };
  if (!event) return { ok: false, error: "Event not found." };

  const suggested = event.llm_suggested_class && event.llm_suggested_class !== event.device_class ? event.llm_suggested_class : null;
  let decision: "accepted" | "rejected" | "not_applicable" = "not_applicable";
  let finalClass = event.device_class as string;
  if (suggested) {
    if (input.reclassDecision !== "accepted" && input.reclassDecision !== "rejected") {
      return { ok: false, error: "Accept or reject the AI reclassification before signing." };
    }
    decision = input.reclassDecision;
    if (decision === "accepted") finalClass = suggested;
  }

  const signedAt = new Date().toISOString();
  const report = normalizeReport(v.report);
  report.sign_off = {
    signed_by: profile.full_name,
    signed_at: signedAt,
    final_class: finalClass as ReportJson["sign_off"]["final_class"],
    reclass_decision: decision,
  };

  const { data, error } = await supabase
    .from("reports")
    .update({ status: "signed", signed_off_by: profile.full_name, signed_off_at: signedAt, report_json: report })
    .eq("id", input.reportId)
    .eq("status", "draft")
    .select("id");
  if (error) return isLocked(error) ? { ok: false, error: LOCKED_MSG, locked: true } : { ok: false, error: error.message };
  if (!data || data.length === 0) return { ok: false, error: LOCKED_MSG, locked: true };

  const { error: evErr } = await supabase.from("events").update({ final_class: finalClass }).eq("id", input.eventId);
  if (evErr) {
    return { ok: false, error: `The report was signed, but the final classification could not be saved: ${evErr.message}` };
  }
  return { ok: true, signedBy: profile.full_name, signedAt, finalClass };
}
