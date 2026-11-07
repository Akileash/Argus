/** Pure helpers for the report review form: paths, diffing against the AI draft, risk scoring, validation. */
import { z } from "zod";
import { ArgusReportSchema, REPORT_FOOTER, type ArgusReport } from "@argus/shared";

export type ReportJson = ArgusReport;

export const SEVERITY_LABELS = ["Negligible", "Minor", "Moderate", "Major", "Catastrophic"] as const;
export const LIKELIHOOD_LABELS = ["Rare", "Unlikely", "Possible", "Likely", "Almost certain"] as const;

/** Risk score = severity x likelihood, clamped to the 1..25 matrix. */
export function computeRiskScore(severity: number, likelihood: number): number {
  const s = Math.min(5, Math.max(1, Math.round(severity)));
  const l = Math.min(5, Math.max(1, Math.round(likelihood)));
  return s * l;
}

/** Return a copy with derived fields recomputed (risk.score) and constants restored (footer). */
export function normalizeReport(r: ReportJson): ReportJson {
  return {
    ...r,
    risk: { ...r.risk, score: computeRiskScore(r.risk.severity, r.risk.likelihood) },
    footer: REPORT_FOOTER,
  };
}

/** The saved report differs from the LLM schema in one place: the manager may overwrite location_on_site. */
const EditableReportSchema = ArgusReportSchema.extend({
  header: ArgusReportSchema.shape.header.extend({ location_on_site: z.string().trim().min(1) }),
});

export function validateReport(r: unknown): { ok: true; report: ReportJson } | { ok: false; error: string } {
  const parsed = EditableReportSchema.safeParse(r);
  if (!parsed.success) {
    const issue = parsed.error.issues[0];
    return { ok: false, error: `Invalid report: ${issue.path.join(".") || "(root)"} ${issue.message}` };
  }
  return { ok: true, report: parsed.data as ReportJson };
}

/* ------------------------------- path helpers ------------------------------- */

export function getAt(obj: unknown, path: string): unknown {
  let cur: unknown = obj;
  for (const seg of path.split(".")) {
    if (cur == null || typeof cur !== "object") return undefined;
    cur = (cur as Record<string, unknown>)[seg];
  }
  return cur;
}

/** Immutable set by dotted path (numeric segments index arrays). */
export function setAt<T>(obj: T, path: string, value: unknown): T {
  const segs = path.split(".");
  const rec = (node: unknown, i: number): unknown => {
    const isArr = Array.isArray(node);
    const key: string | number = isArr ? Number(segs[i]) : segs[i];
    const copy: any = isArr ? [...(node as unknown[])] : { ...((node as object) ?? {}) };
    copy[key] = i === segs.length - 1 ? value : rec(copy[key], i + 1);
    return copy;
  };
  return rec(obj, 0) as T;
}

/** Leaf-level flattening: {"a.b": 1, "list.0.x": "y"}. */
export function flattenLeaves(value: unknown, prefix = "", out: Record<string, unknown> = {}): Record<string, unknown> {
  if (Array.isArray(value)) {
    value.forEach((v, i) => flattenLeaves(v, prefix ? `${prefix}.${i}` : String(i), out));
  } else if (value && typeof value === "object") {
    for (const [k, v] of Object.entries(value)) flattenLeaves(v, prefix ? `${prefix}.${k}` : k, out);
  } else {
    out[prefix] = value;
  }
  return out;
}

/** Paths whose value differs between the AI draft and the current report. sign_off.* and the derived risk.score are not edits. */
export function diffPaths(draft: unknown, current: unknown): Set<string> {
  const a = flattenLeaves(draft);
  const b = flattenLeaves(current);
  const out = new Set<string>();
  for (const k of new Set([...Object.keys(a), ...Object.keys(b)])) {
    if (k.startsWith("sign_off") || k === "risk.score") continue;
    if (a[k] !== b[k]) out.add(k);
  }
  return out;
}

/** True when `path` or anything under it was edited. */
export function sectionEdited(edited: Set<string>, prefix: string): boolean {
  for (const p of edited) if (p === prefix || p.startsWith(prefix + ".")) return true;
  return false;
}

/* --------------------------- form field mapping --------------------------- */

export type FieldKind = "text" | "textarea" | "select";
export interface FieldSpec {
  path: string;
  label: string;
  kind: FieldKind;
  options?: readonly { value: string | number; label: string }[];
}

export const HAZARD_OPTIONS = [
  { value: "struck_by", label: "Struck by" },
  { value: "caught_between", label: "Caught between" },
  { value: "fall_from_height", label: "Fall from height" },
  { value: "slip_trip_fall", label: "Slip / trip / fall" },
  { value: "falling_object", label: "Falling object" },
  { value: "ppe", label: "PPE" },
  { value: "other", label: "Other" },
] as const;

export const CONTROL_OPTIONS = [
  { value: "elimination", label: "Elimination" },
  { value: "substitution", label: "Substitution" },
  { value: "engineering", label: "Engineering" },
  { value: "administrative", label: "Administrative" },
  { value: "ppe", label: "PPE" },
] as const;

export const CAUSE_KIND_OPTIONS = [
  { value: "substandard_act", label: "Substandard act" },
  { value: "substandard_condition", label: "Substandard condition" },
] as const;

export const SOURCE_OPTIONS = [
  { value: "sensor", label: "Sensor" },
  { value: "video", label: "Video" },
  { value: "inferred", label: "Inferred" },
] as const;

const scale = (labels: readonly string[]) => labels.map((l, i) => ({ value: i + 1, label: `${i + 1} ${l}` }));

/** Scalar fields of the form. List sections use the same dotted-path scheme. */
export const SCALAR_FIELDS: Record<string, FieldSpec> = {
  location: { path: "header.location_on_site", label: "Location on site", kind: "text" },
  summary: { path: "summary", label: "Summary", kind: "textarea" },
  environment: { path: "evidence.environment", label: "Environmental conditions", kind: "textarea" },
  hazardCategory: { path: "hazard.category", label: "Hazard category", kind: "select", options: HAZARD_OPTIONS },
  energy: { path: "hazard.energy_source", label: "Energy source", kind: "text" },
  severity: { path: "risk.severity", label: "Severity", kind: "select", options: scale(SEVERITY_LABELS) },
  likelihood: { path: "risk.likelihood", label: "Likelihood", kind: "select", options: scale(LIKELIHOOD_LABELS) },
  severityWhy: { path: "risk.severity_justification", label: "Severity justification", kind: "textarea" },
  likelihoodWhy: { path: "risk.likelihood_justification", label: "Likelihood justification", kind: "textarea" },
  regulatory: { path: "regulatory_note", label: "Regulatory note", kind: "textarea" },
  gaps: { path: "confidence_and_gaps", label: "Confidence and gaps", kind: "textarea" },
};

/** Form (string) value of a field. */
export function fieldValue(report: unknown, path: string): string {
  const v = getAt(report, path);
  return v == null ? "" : String(v);
}

/** Write a raw DOM string back into the report, coercing numeric selects; recomputes risk.score when risk inputs change. */
export function applyFieldValue(report: ReportJson, spec: FieldSpec, raw: string): ReportJson {
  const numeric = spec.kind === "select" && typeof spec.options?.[0]?.value === "number";
  const next = setAt(report, spec.path, numeric ? Number(raw) : raw);
  return spec.path.startsWith("risk.") ? normalizeReport(next) : next;
}
