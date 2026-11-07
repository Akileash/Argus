/**
 * Deterministic sample payloads for tests and the mock demo. Not used in production paths.
 * Shapes are checked against both zod and the JSON Schemas in test/contracts.test.ts.
 */
import { EVENT_TYPE_TO_CLASS, LOCATION_PLACEHOLDER, REPORT_FOOTER, type EventType } from "./constants";
import type { ArgusEvent, ArgusReport, SensorTrace } from "./schemas";

export const FIXTURE_EVENT_ID = "6f1d2c1a-3b4e-4f5a-8b6c-7d8e9f0a1b2c";
export const FIXTURE_T0 = "2026-06-12T15:42:07.000Z";
const SHA = "a".repeat(64);

type MetricsPatch = Partial<ArgusEvent["metrics"]>;

const METRICS: Record<EventType, MetricsPatch> = {
  struck_by_near_miss: { peak_g: null, closing_rate_mps: 4.2, ttc_s: 0.8, range_m: 3.4, sensor_id: "R2", hat_on: true },
  struck_by_contact: { peak_g: 9.5, peak_g_saturated: false, closing_rate_mps: 3.1, ttc_s: 0, range_m: 0.1, sensor_id: "R3", hat_on: true },
  head_impact: { peak_g: 16, peak_g_saturated: true, closing_rate_mps: null, ttc_s: null, range_m: null, sensor_id: null, hat_on: true },
  fall: { peak_g: 12.4, peak_g_saturated: false, free_fall_ms: 450, drop_height_m: 0.99, hat_on: true },
  hat_knocked_off: { peak_g: 7.2, peak_g_saturated: false, hat_on: false },
  hat_removed: { hat_on: false },
  module_fallback: { hat_on: true },
};

export function makeEvent(type: EventType = "struck_by_near_miss", eventId: string = FIXTURE_EVENT_ID): ArgusEvent {
  const hasMedia = type !== "hat_removed" && type !== "module_fallback";
  return {
    schema_version: "1",
    event_id: eventId,
    device_serial: "ARGUS-001",
    type,
    device_class: EVENT_TYPE_TO_CLASS[type],
    t0: FIXTURE_T0,
    detected_at: "2026-06-12T15:42:07.350Z",
    window: { pre_s: 20, post_s: 10 },
    metrics: { ...METRICS[type] } as ArgusEvent["metrics"],
    env: { co2_ppm: 612, temp_c: 21.4, rh_pct: 38 },
    merged_triggers: type === "struck_by_contact" ? [{ type: "head_impact", offset_s: 0.4 }] : [],
    media: hasMedia
      ? [
          { kind: "clip", camera: "front", filename: `${eventId}.front.mp4`, content_type: "video/mp4", bytes: 11_000_000, sha256: SHA, duration_s: 30 },
          { kind: "sensor_trace", filename: `${eventId}.trace.json`, content_type: "application/json", bytes: 48_000, sha256: SHA, duration_s: null },
        ]
      : [],
    firmware: { version: "0.1.0", impls: { imu: "mock", range: "mock" }, fallbacks_active: [] },
  };
}

function series(from: number, to: number, step: number): number[] {
  const out: number[] = [];
  for (let t = from; t <= to + 1e-9; t += step) out.push(Math.round(t * 100) / 100);
  return out;
}

export function makeTrace(type: EventType = "struck_by_near_miss", eventId: string = FIXTURE_EVENT_ID): SensorTrace {
  const ev = makeEvent(type, eventId);
  const t = series(-3, 3, 0.1);
  const peak = ev.metrics.peak_g ?? 1.4;
  const a = t.map((x) => (Math.abs(x) < 0.05 ? peak : 1 + 0.02 * Math.sin(x * 9)));
  const zeros = t.map(() => 0);
  const m = ev.metrics;
  const sid = m.sensor_id ?? null;

  // Detection names and fields mirror what device/argus/events/trace.py really writes.
  const detections: SensorTrace["detections"] = [];
  if (type === "struck_by_near_miss" || type === "struck_by_contact") {
    for (const [i, tt] of [-0.9, -0.84, -0.78, -0.72].entries()) {
      detections.push({ t: tt, type: "struck_by_warning", sensor_id: sid, closing_rate_mps: m.closing_rate_mps, ttc_s: Math.round((0.9 - i * 0.06) * 100) / 100, range_m: Math.round((3.4 - i * 0.25) * 100) / 100 });
    }
    detections.push({ t: 0, type: type === "struck_by_near_miss" ? "struck_by_near_miss" : "struck_by_contact", sensor_id: sid, closing_rate_mps: m.closing_rate_mps, ttc_s: m.ttc_s, range_m: m.range_m });
  }
  if (type === "struck_by_contact" || type === "head_impact") {
    detections.push({ t: 0, type: "head_impact", sensor_id: null, peak_g: m.peak_g, ...(m.peak_g_saturated ? { peak_g_saturated: true } : {}) });
  }
  if (type === "fall") detections.push({ t: 0, type: "fall", sensor_id: null, peak_g: m.peak_g, free_fall_ms: m.free_fall_ms, drop_height_m: m.drop_height_m });
  if (type === "hat_knocked_off") detections.push({ t: 0, type: "hat_knocked_off", sensor_id: null, peak_g: m.peak_g });
  if (type === "hat_removed") detections.push({ t: 0, type: "hat_removed", sensor_id: null });

  const ranges: SensorTrace["ranges"] = {};
  if (sid && sid !== "TOP") {
    ranges[sid] = {
      t,
      range_m: t.map((x) => (x < -2 ? null : Math.max(0.1, Math.round((3.4 - 4.2 * (x + 2) * 0.55) * 100) / 100))),
      closing_mps: t.map((x) => (x < -2 ? null : 4.2)),
    };
  }

  return {
    schema_version: "1",
    event_id: eventId,
    t_start_s: -20,
    t_end_s: 10,
    imu: { t, a_mag_g: a, ax: zeros, ay: zeros, az: a, gx: zeros, gy: zeros, gz: zeros },
    imu_peaks: m.peak_g ? [{ t: 0, a_mag_g: m.peak_g, saturated: m.peak_g_saturated ?? false }] : [],
    ranges,
    tof: { t, range_mm: t.map(() => 62) },
    hat_on: type === "hat_knocked_off" ? [{ t: -20, on: true }, { t: 0.2, on: false }] : type === "hat_removed" ? [{ t: -20, on: true }, { t: 0, on: false }] : [{ t: -20, on: true }],
    haptics: type === "struck_by_near_miss" || type === "struck_by_contact" ? [{ t: -0.88, motor: "back", pattern: "pulse_4hz", duration_s: 0.8 }] : type === "hat_removed" || type === "module_fallback" ? [] : [{ t: 0.1, motor: "both", pattern: "ack_1500ms", duration_s: 1.5 }],
    detections,
    env: { co2_ppm: 612, temp_c: 21.4, rh_pct: 38 },
  };
}

export function makeReport(overrides: Partial<ArgusReport> = {}): ArgusReport {
  return {
    schema_version: "1",
    header: {
      report_no: "ARG-2026-0042",
      classification: "near_miss",
      occurred_at: FIXTURE_T0,
      site: "Example Project - Edmonton",
      location_on_site: LOCATION_PLACEHOLDER,
      worker: "J. Smith",
      crew: "Framing A",
      supervisor: "A. Tremblay",
      device: "ARGUS-001",
    },
    summary: "A fast-approaching object was detected behind the worker. No contact was recorded.",
    sequence_of_events: [{ offset_s: -0.9, description: "Rear sensor R2 flagged an approaching object.", source: "sensor" }],
    evidence: {
      clip_refs: [{ offset_s: -0.9, observation: "Object enters the frame edge." }],
      key_sensor_values: [{ name: "closing_rate_mps", value: "4.2" }],
      environment: "Near-wearer conditions (indicative): CO2 612 ppm.",
    },
    hazard: { category: "struck_by", energy_source: "Moving object (kinetic)" },
    risk: { severity: 4, likelihood: 3, score: 12, severity_justification: "Head strike possible.", likelihood_justification: "Repeated exposure." },
    causes: { immediate: [{ kind: "substandard_condition", description: "Mobile equipment working behind the worker." }], root_five_whys: ["No spotter assigned."] },
    corrective_actions: [{ description: "Assign a spotter.", control_level: "administrative", suggested_owner_role: "Site supervisor" }],
    regulatory_note: "May meet a dangerous-occurrence threshold; verify with your provincial OHS regulator.",
    confidence_and_gaps: "Rear view not available.",
    classification_review: { suggested_class: null, reason: null },
    sign_off: { signed_by: null, signed_at: null, final_class: null, reclass_decision: null },
    footer: REPORT_FOOTER,
    ...overrides,
  };
}
