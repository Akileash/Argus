import { describe, expect, it } from "vitest";
import { REPORT_FOOTER, makeReport } from "@argus/shared";
import {
  SCALAR_FIELDS,
  applyFieldValue,
  computeRiskScore,
  diffPaths,
  fieldValue,
  getAt,
  normalizeReport,
  sectionEdited,
  setAt,
  validateReport,
} from "@/lib/report/model";

const base = () => makeReport();

describe("risk score", () => {
  it("multiplies severity by likelihood", () => {
    expect(computeRiskScore(3, 4)).toBe(12);
    expect(computeRiskScore(5, 5)).toBe(25);
    expect(computeRiskScore(1, 1)).toBe(1);
  });
  it("clamps out-of-range inputs to the 5x5 matrix", () => {
    expect(computeRiskScore(9, 0)).toBe(5);
    expect(computeRiskScore(-2, 7)).toBe(5);
  });
  it("normalizeReport recomputes the score and restores the footer", () => {
    const r = base();
    r.risk.severity = 4;
    r.risk.likelihood = 5;
    r.risk.score = 1;
    (r as { footer: string }).footer = "tampered";
    const n = normalizeReport(r);
    expect(n.risk.score).toBe(20);
    expect(n.footer).toBe(REPORT_FOOTER);
  });
});

describe("form mapping", () => {
  it("reads every scalar field from the report", () => {
    const r = base();
    for (const spec of Object.values(SCALAR_FIELDS)) {
      expect(getAt(r, spec.path), spec.path).not.toBeUndefined();
      expect(typeof fieldValue(r, spec.path)).toBe("string");
    }
  });
  it("writes text fields back by path", () => {
    const r = applyFieldValue(base(), SCALAR_FIELDS.summary, "New summary");
    expect(r.summary).toBe("New summary");
  });
  it("coerces numeric selects and recomputes score on risk changes", () => {
    let r = applyFieldValue(base(), SCALAR_FIELDS.severity, "5");
    expect(r.risk.severity).toBe(5);
    r = applyFieldValue(r, SCALAR_FIELDS.likelihood, "3");
    expect(r.risk.likelihood).toBe(3);
    expect(r.risk.score).toBe(15);
  });
  it("setAt is immutable and handles array indices", () => {
    const r = base();
    const next = setAt(r, "sequence_of_events.0.description", "changed");
    expect(next.sequence_of_events[0].description).toBe("changed");
    expect(r.sequence_of_events[0].description).not.toBe("changed");
  });
});

describe("edited detection (diff against llm_draft_json)", () => {
  it("is empty when nothing changed", () => {
    expect(diffPaths(base(), base()).size).toBe(0);
  });
  it("flags only the changed leaves", () => {
    const edited = setAt(setAt(base(), "summary", "Edited by me"), "header.location_on_site", "Level 3, east stair");
    const d = diffPaths(base(), edited);
    expect([...d].sort()).toEqual(["header.location_on_site", "summary"]);
    expect(sectionEdited(d, "header")).toBe(true);
    expect(sectionEdited(d, "hazard")).toBe(false);
  });
  it("ignores sign_off and derived risk.score", () => {
    const r = base();
    const signed = {
      ...r,
      sign_off: { signed_by: "A", signed_at: "2026-06-12T16:00:00.000Z", final_class: "near_miss" as const, reclass_decision: "rejected" as const },
      risk: { ...r.risk, score: 99 },
    };
    expect(diffPaths(base(), signed).size).toBe(0);
  });
  it("flags added and removed list items", () => {
    const added = setAt(base(), "corrective_actions", [
      ...base().corrective_actions,
      { description: "x", control_level: "ppe", suggested_owner_role: "Foreman" },
    ]);
    expect([...diffPaths(base(), added)].some((p) => p.startsWith("corrective_actions."))).toBe(true);
    const removed = setAt(base(), "sequence_of_events", []);
    expect([...diffPaths(base(), removed)].some((p) => p.startsWith("sequence_of_events."))).toBe(true);
  });
  it("reverting a field clears the edit marker", () => {
    const orig = base();
    const edited = setAt(orig, "summary", "x");
    const reverted = setAt(edited, "summary", getAt(orig, "summary"));
    expect(diffPaths(orig, reverted).size).toBe(0);
  });
});

describe("validateReport", () => {
  it("accepts a draft and a manager-edited location", () => {
    expect(validateReport(base()).ok).toBe(true);
    expect(validateReport(setAt(base(), "header.location_on_site", "Level 3")).ok).toBe(true);
  });
  it("rejects empty location, bad enums and a changed footer", () => {
    expect(validateReport(setAt(base(), "header.location_on_site", "  ")).ok).toBe(false);
    expect(validateReport(setAt(base(), "hazard.category", "nope")).ok).toBe(false);
    expect(validateReport(setAt(base(), "footer", "changed")).ok).toBe(false);
  });
});
