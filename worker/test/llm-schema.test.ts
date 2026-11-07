import { describe, expect, it } from "vitest";
import { makeReport } from "@argus/shared";
import { LLM_SCHEMA, assertValidReport, simplifySchema } from "../src/llm-schema";

describe("LLM schema", () => {
  it("has no keywords structured-output engines reject and no type arrays", () => {
    const s = JSON.stringify(LLM_SCHEMA);
    for (const bad of ['"format"', '"minimum"', '"maximum"', '"$schema"', '"const"']) expect(s).not.toContain(bad);
    expect(s).not.toMatch(/"type":\[/);
    expect(s).not.toContain("null,"); // no enum containing null
  });
  it("excludes worker-owned keys and risk.score", () => {
    const p = (LLM_SCHEMA as any).properties;
    for (const k of ["header", "sign_off", "footer", "schema_version"]) expect(p[k]).toBeUndefined();
    expect(p.risk.properties.score).toBeUndefined();
    expect(p.risk.required).not.toContain("score");
  });
  it("turns nullable enums into anyOf", () => {
    expect(simplifySchema({ enum: ["a", null] })).toEqual({ anyOf: [{ enum: ["a"] }, { type: "null" }] });
    expect(simplifySchema({ type: ["string", "null"] })).toEqual({ anyOf: [{ type: "string" }, { type: "null" }] });
  });
  it("assertValidReport accepts a good report and rejects a bad footer", () => {
    expect(() => assertValidReport(makeReport())).not.toThrow();
    expect(() => assertValidReport({ ...makeReport(), footer: "x" })).toThrow();
  });
});
