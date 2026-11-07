import Ajv2020, { type ErrorObject } from "ajv/dist/2020";
import addFormats from "ajv-formats";
import eventSchema from "../../../contracts/event.schema.json";

import type { ArgusEvent, EventType } from "@argus/shared";

export type { ArgusEvent, EventType, EventClass } from "@argus/shared";
export type ArgusMedia = ArgusEvent["media"][number];

/** Types that go straight received -> ready (no media, no report). */
export const SHORT_CIRCUIT_TYPES: ReadonlySet<EventType> = new Set(["hat_removed", "module_fallback"]);

const ajv = new Ajv2020({ allErrors: true, strict: false });
addFormats(ajv);
const validateFn = ajv.compile<ArgusEvent>(eventSchema);

export type ValidationResult = { ok: true; event: ArgusEvent } | { ok: false; errors: ErrorObject[] };

export function validateEvent(body: unknown): ValidationResult {
  if (validateFn(body)) return { ok: true, event: body };
  return { ok: false, errors: [...(validateFn.errors ?? [])] };
}
