import Ajv2020, { type ErrorObject } from "ajv/dist/2020";
import addFormats from "ajv-formats";
import eventSchema from "../../../contracts/event.schema.json";

// TODO(PM): swap for @argus/shared once it exists in this workspace.

export type EventType =
  | "struck_by_near_miss"
  | "struck_by_contact"
  | "head_impact"
  | "fall"
  | "hat_knocked_off"
  | "hat_removed"
  | "module_fallback";
export type EventClass = "near_miss" | "incident" | "ppe_log" | "system";

export interface ArgusMedia {
  kind: "clip" | "sensor_trace";
  camera?: "front" | null;
  filename: string;
  content_type: "video/mp4" | "application/json";
  bytes: number;
  sha256: string;
  duration_s?: number | null;
}

export interface ArgusEvent {
  schema_version: "1";
  event_id: string;
  device_serial: string;
  type: EventType;
  device_class: EventClass;
  t0: string;
  detected_at: string;
  window: { pre_s: number; post_s: number };
  metrics: {
    peak_g?: number | null;
    peak_g_saturated?: boolean;
    closing_rate_mps?: number | null;
    ttc_s?: number | null;
    range_m?: number | null;
    sensor_id?: "R1" | "R2" | "R3" | "R4" | "TOP" | null;
    free_fall_ms?: number | null;
    drop_height_m?: number | null;
    hat_on: boolean;
  };
  env?: { co2_ppm?: number | null; temp_c?: number | null; rh_pct?: number | null } | null;
  merged_triggers?: { type: EventType; offset_s: number }[];
  media: ArgusMedia[];
  firmware: { version: string; impls: Record<string, "argus" | "team" | "mock" | "replay">; fallbacks_active?: string[] };
}

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
