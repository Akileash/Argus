import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { ArgusReportSchema, type ArgusReport } from "@argus/shared";
import reportContract from "../../contracts/report.schema.json";

type Json = Record<string, any>;

// Imported (not read from disk at runtime) so the contract is bundled into the Next/Vercel build.
const reportSchema = reportContract as Json;

/** Top-level report keys the worker owns. The LLM is never asked for them. */
export const WORKER_OWNED_KEYS = ["schema_version", "header", "sign_off", "footer"] as const;
/** The LLM-written analysis fields. */
export const LLM_KEYS = [
  "summary",
  "sequence_of_events",
  "evidence",
  "hazard",
  "risk",
  "causes",
  "corrective_actions",
  "regulatory_note",
  "confidence_and_gaps",
  "classification_review",
] as const;
export type LlmKey = (typeof LLM_KEYS)[number];
export type LlmFields = Pick<ArgusReport, LlmKey>;

const STRIP = new Set(["$schema", "$id", "title", "format", "minimum", "maximum", "minLength", "maxLength", "default"]);

/**
 * Make a JSON Schema acceptable to structured-output engines (Claude output_config.format and Gemini
 * responseJsonSchema): drop validation keywords they reject, and turn `type: [x, "null"]` and enums
 * containing null into anyOf. The final object is still validated against the REAL contract schema.
 */
export function simplifySchema(node: unknown): unknown {
  if (Array.isArray(node)) return node.map(simplifySchema);
  if (node === null || typeof node !== "object") return node;
  const src = node as Json;
  const out: Json = {};
  for (const [k, v] of Object.entries(src)) {
    if (STRIP.has(k)) continue;
    if (k === "properties") {
      out[k] = Object.fromEntries(Object.entries(v as Json).map(([pk, pv]) => [pk, simplifySchema(pv)]));
    } else {
      out[k] = simplifySchema(v);
    }
  }
  if (Array.isArray(out.type)) {
    const types = out.type as string[];
    const rest = { ...out };
    delete rest.type;
    return { ...rest, anyOf: types.map((t) => ({ type: t })) };
  }
  if (Array.isArray(out.enum) && out.enum.includes(null)) {
    const vals = (out.enum as unknown[]).filter((x) => x !== null);
    const rest = { ...out };
    delete rest.enum;
    return { ...rest, anyOf: [{ enum: vals }, { type: "null" }] };
  }
  return out;
}

/** JSON Schema for the LLM-written subset: report minus header/sign_off/footer and risk.score. */
export function buildLlmSchema(): Json {
  const props: Json = {};
  for (const key of LLM_KEYS) props[key] = structuredClone(reportSchema.properties[key]);
  const risk = props.risk;
  delete risk.properties.score;
  risk.required = (risk.required as string[]).filter((r) => r !== "score");
  return simplifySchema({
    type: "object",
    additionalProperties: false,
    required: [...LLM_KEYS],
    properties: props,
  }) as Json;
}

export const LLM_SCHEMA = buildLlmSchema();

/** Observation timeline from Gemini stage 1 (hybrid mode). */
export const OBSERVATION_SCHEMA: Json = {
  type: "object",
  additionalProperties: false,
  required: ["observations", "contact_visible", "visibility_notes"],
  properties: {
    observations: {
      type: "array",
      items: {
        type: "object",
        additionalProperties: false,
        required: ["video_time_s", "category", "observation"],
        properties: {
          video_time_s: { type: "number", description: "Seconds from the start of the clip." },
          category: { enum: ["object_motion", "person", "worker_activity", "ppe", "environment", "contact", "other"] },
          observation: { type: "string" },
        },
      },
    },
    contact_visible: { enum: ["yes", "no", "unclear"], description: "Is a physical contact with the camera wearer's head or hat visible or audible?" },
    visibility_notes: { type: "string", description: "Blur, darkness, obstruction, or what is out of frame." },
  },
};
export interface Observations {
  observations: Array<{ video_time_s: number; category: string; observation: string }>;
  contact_visible: "yes" | "no" | "unclear";
  visibility_notes: string;
}

const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
const validateContract = ajv.compile(reportSchema);

/** Validate against zod AND the real JSON Schema. Throws with readable details. */
export function assertValidReport(report: unknown): ArgusReport {
  const z = ArgusReportSchema.safeParse(report);
  if (!z.success) {
    throw new Error(`report failed zod validation: ${z.error.issues.slice(0, 5).map((i) => `${i.path.join(".")}: ${i.message}`).join("; ")}`);
  }
  if (!validateContract(report)) {
    throw new Error(`report failed JSON Schema validation: ${ajv.errorsText(validateContract.errors)}`);
  }
  return z.data;
}
