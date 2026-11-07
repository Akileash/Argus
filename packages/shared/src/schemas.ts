/**
 * zod mirrors of contracts/{event,report,sensor_trace}.schema.json.
 * test/contracts.test.ts validates the same fixtures against BOTH these and the JSON Schemas
 * so they cannot drift. If a contract changes, update here and the test will tell you.
 */
import { z } from "zod";
import { EVENT_CLASSES, EVENT_TYPES, LOCATION_PLACEHOLDER, REPORT_FOOTER } from "./constants";

const dateTime = z.string().datetime({ offset: true });
const nullableNum = z.number().nullable();

/* ------------------------------ event.schema.json ------------------------------ */

export const EventTypeSchema = z.enum(EVENT_TYPES);
export const EventClassSchema = z.enum(EVENT_CLASSES);

export const ArgusEventSchema = z
  .object({
    schema_version: z.literal("1"),
    event_id: z.string().uuid(),
    device_serial: z.string().min(1),
    type: EventTypeSchema,
    device_class: EventClassSchema,
    t0: dateTime,
    detected_at: dateTime,
    window: z.object({ pre_s: z.number().min(0), post_s: z.number().min(0) }).strict(),
    metrics: z
      .object({
        peak_g: z.number().min(0).nullable().optional(),
        peak_g_saturated: z.boolean().optional(),
        closing_rate_mps: nullableNum.optional(),
        ttc_s: z.number().min(0).nullable().optional(),
        range_m: z.number().min(0).nullable().optional(),
        sensor_id: z.enum(["R1", "R2", "R3", "R4", "TOP"]).nullable().optional(),
        free_fall_ms: z.number().min(0).nullable().optional(),
        drop_height_m: z.number().min(0).nullable().optional(),
        hat_on: z.boolean(),
      })
      .strict(),
    env: z
      .object({
        co2_ppm: nullableNum.optional(),
        temp_c: nullableNum.optional(),
        rh_pct: nullableNum.optional(),
      })
      .strict()
      .nullable()
      .optional(),
    merged_triggers: z
      .array(z.object({ type: EventTypeSchema, offset_s: z.number() }).strict())
      .optional(),
    media: z.array(
      z
        .object({
          kind: z.enum(["clip", "sensor_trace"]),
          camera: z.literal("front").nullable().optional(),
          filename: z.string(),
          content_type: z.enum(["video/mp4", "application/json"]),
          bytes: z.number().int().min(0),
          sha256: z.string().regex(/^[a-f0-9]{64}$/),
          duration_s: nullableNum.optional(),
        })
        .strict(),
    ),
    firmware: z
      .object({
        version: z.string(),
        impls: z.record(z.enum(["argus", "team", "mock", "replay"])),
        fallbacks_active: z.array(z.string()).optional(),
      })
      .strict(),
  })
  .strict();
export type ArgusEvent = z.infer<typeof ArgusEventSchema>;

/* --------------------------- sensor_trace.schema.json --------------------------- */

const nums = z.array(z.number());
const nullableNums = z.array(z.number().nullable());
const SensorIdSchema = z.enum(["R1", "R2", "R3", "R4", "TOP"]);

export const SensorTraceSchema = z
  .object({
    schema_version: z.literal("1"),
    event_id: z.string().uuid(),
    t_start_s: z.number(),
    t_end_s: z.number(),
    imu: z
      .object({
        t: nums,
        a_mag_g: nums,
        ax: nums,
        ay: nums,
        az: nums,
        gx: nums,
        gy: nums,
        gz: nums,
      })
      .strict(),
    imu_peaks: z
      .array(z.object({ t: z.number(), a_mag_g: z.number(), saturated: z.boolean() }).strict())
      .optional(),
    ranges: z.record(
      SensorIdSchema,
      z.object({ t: nums, range_m: nullableNums, closing_mps: nullableNums }).strict(),
    ),
    tof: z.object({ t: nums, range_mm: nullableNums }).strict(),
    hat_on: z.array(z.object({ t: z.number(), on: z.boolean() }).strict()),
    haptics: z.array(
      z
        .object({
          t: z.number(),
          motor: z.enum(["top", "back", "both"]),
          pattern: z.string(),
          duration_s: z.number(),
        })
        .strict(),
    ),
    detections: z.array(z.object({ t: z.number(), type: z.string() }).passthrough()),
    env: z
      .object({
        co2_ppm: nullableNum.optional(),
        temp_c: nullableNum.optional(),
        rh_pct: nullableNum.optional(),
      })
      .strict()
      .nullable()
      .optional(),
  })
  .strict();
export type SensorTrace = z.infer<typeof SensorTraceSchema>;

/* ----------------------------- report.schema.json ----------------------------- */

export const HazardCategories = [
  "struck_by",
  "caught_between",
  "fall_from_height",
  "slip_trip_fall",
  "falling_object",
  "ppe",
  "other",
] as const;
export const ControlLevels = ["elimination", "substitution", "engineering", "administrative", "ppe"] as const;

export const ArgusReportSchema = z
  .object({
    schema_version: z.literal("1"),
    header: z
      .object({
        report_no: z.string(),
        classification: z.enum(["near_miss", "incident", "ppe_log"]),
        occurred_at: dateTime,
        site: z.string(),
        location_on_site: z.literal(LOCATION_PLACEHOLDER),
        worker: z.string(),
        crew: z.string(),
        supervisor: z.string(),
        device: z.string(),
      })
      .strict(),
    summary: z.string(),
    sequence_of_events: z.array(
      z
        .object({
          offset_s: z.number(),
          description: z.string(),
          source: z.enum(["sensor", "video", "inferred"]),
        })
        .strict(),
    ),
    evidence: z
      .object({
        clip_refs: z.array(z.object({ offset_s: z.number(), observation: z.string() }).strict()),
        key_sensor_values: z.array(z.object({ name: z.string(), value: z.string() }).strict()),
        environment: z.string(),
      })
      .strict(),
    hazard: z.object({ category: z.enum(HazardCategories), energy_source: z.string() }).strict(),
    risk: z
      .object({
        severity: z.number().int().min(1).max(5),
        likelihood: z.number().int().min(1).max(5),
        score: z.number().int().min(1).max(25),
        severity_justification: z.string(),
        likelihood_justification: z.string(),
      })
      .strict(),
    causes: z
      .object({
        immediate: z.array(
          z
            .object({
              kind: z.enum(["substandard_act", "substandard_condition"]),
              description: z.string(),
            })
            .strict(),
        ),
        root_five_whys: z.array(z.string()),
      })
      .strict(),
    corrective_actions: z.array(
      z
        .object({
          description: z.string(),
          control_level: z.enum(ControlLevels),
          suggested_owner_role: z.string(),
        })
        .strict(),
    ),
    regulatory_note: z.string(),
    confidence_and_gaps: z.string(),
    classification_review: z
      .object({
        suggested_class: z.enum(["near_miss", "incident"]).nullable(),
        reason: z.string().nullable(),
      })
      .strict(),
    sign_off: z
      .object({
        signed_by: z.string().nullable(),
        signed_at: dateTime.nullable(),
        final_class: z.enum(["near_miss", "incident", "ppe_log"]).nullable(),
        reclass_decision: z.enum(["accepted", "rejected", "not_applicable"]).nullable(),
      })
      .strict(),
    footer: z.literal(REPORT_FOOTER),
  })
  .strict();
export type ArgusReport = z.infer<typeof ArgusReportSchema>;
