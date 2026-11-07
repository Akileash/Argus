import type { SensorTrace, EventType } from "@argus/shared";
import type { LlmFields } from "../llm-schema";
import type { EventContext } from "../store/types";
import { environmentText, fmtOffset, keySensorValues, sensorTimeline } from "./summary";

type Hazard = LlmFields["hazard"];
type Action = LlmFields["corrective_actions"][number];
type Cause = LlmFields["causes"]["immediate"][number];

const f = (n: number | null | undefined, d = 1) => (n == null ? "n/a" : n.toFixed(d));

interface Template {
  hazard: Hazard;
  immediate: Cause[];
  whys: string[];
  actions: Action[];
}

const REAR_ACTIONS: Action[] = [
  { description: "Separate pedestrian routes from mobile equipment and material-handling paths with barriers or exclusion zones.", control_level: "engineering", suggested_owner_role: "Site superintendent" },
  { description: "Assign a spotter or signaller when equipment or loads move near workers, and confirm eye contact before moving.", control_level: "administrative", suggested_owner_role: "Site supervisor" },
  { description: "Review the traffic and lifting plan with the crew at the next toolbox talk, using this event as the example.", control_level: "administrative", suggested_owner_role: "Health and safety coordinator" },
];

function template(type: EventType, ctx: EventContext): Template {
  const m = ctx.event.metrics;
  switch (type) {
    case "struck_by_near_miss":
    case "struck_by_contact":
      return {
        hazard: { category: "struck_by", energy_source: "Kinetic energy of a moving object (equipment, vehicle or material) approaching from behind the worker" },
        immediate: [
          { kind: "substandard_condition", description: "An object moved into the worker's blind zone behind them without a warning the worker could act on." },
          { kind: "substandard_act", description: "The worker's attention was on the task ahead; the approach was only detected by the hat's rear sensors." },
        ],
        whys: [
          "An object approached the worker from behind at speed.",
          "The worker could not see or hear it in time while focused on the task ahead.",
          "The work area is shared by people and moving objects without a separating control.",
          "Movement of equipment and materials is not coordinated with workers on foot.",
          "The traffic and material-handling plan does not set exclusion zones or require a spotter for this area.",
        ],
        actions: REAR_ACTIONS,
      };
    case "head_impact":
      return {
        hazard: { category: m.sensor_id === "TOP" ? "falling_object" : "struck_by", energy_source: "Kinetic or gravitational energy of an object striking the hard hat" },
        immediate: [
          { kind: "substandard_condition", description: "An object struck the hard hat; its source is not identified by the sensors alone." },
        ],
        whys: [
          "The hard hat was struck hard enough to register as a head impact.",
          "An object or structure was in the worker's head-level or overhead space.",
          "Overhead or head-level hazards were not controlled in this work area.",
          "The area was not assessed for overhead work or protrusions before workers entered.",
        ],
        actions: [
          { description: "Establish overhead-work exclusion zones and use toe boards, debris netting or tethers for tools and materials above workers.", control_level: "engineering", suggested_owner_role: "Site superintendent" },
          { description: "Have the worker assessed by a first aider or medical professional, even if no symptoms are apparent, and record the outcome.", control_level: "administrative", suggested_owner_role: "Health and safety coordinator" },
          { description: "Inspect the hard hat for damage and replace it if the shell or suspension was affected.", control_level: "ppe", suggested_owner_role: "Site supervisor" },
        ],
      };
    case "fall": {
      const high = (m.drop_height_m ?? 0) >= 1;
      return {
        hazard: { category: high ? "fall_from_height" : "slip_trip_fall", energy_source: "Gravitational energy of the worker falling" },
        immediate: [
          { kind: "substandard_condition", description: "The sensors recorded a free fall followed by an impact; the surface or edge condition is not determined from sensor data alone." },
        ],
        whys: [
          "The worker fell and the hat recorded a free fall and an impact.",
          "The worker lost footing or support at the point of the fall.",
          "The walking or working surface or access was not controlled for this task.",
          "Housekeeping, edge protection or access equipment did not prevent the fall.",
        ],
        actions: [
          { description: "Provide guardrails, covers or a safe access method at the location of the fall, or eliminate the need to work there.", control_level: "engineering", suggested_owner_role: "Site superintendent" },
          { description: "Inspect the area for slip, trip and edge hazards and correct them before work resumes.", control_level: "administrative", suggested_owner_role: "Site supervisor" },
          { description: "Arrange a medical assessment for the worker and review fall-protection requirements for this task with the crew.", control_level: "administrative", suggested_owner_role: "Health and safety coordinator" },
        ],
      };
    }
    case "hat_knocked_off":
      return {
        hazard: { category: "other", energy_source: "Force that displaced the hard hat from the worker's head (cause not identified from sensor data)" },
        immediate: [
          { kind: "substandard_condition", description: "The hard hat was displaced during the event, leaving the head unprotected afterward." },
        ],
        whys: [
          "The hard hat came off the worker's head after an impact.",
          "The hat was not retained by a chin strap or fit loosely.",
          "Hard-hat fit and retention was not verified for this task.",
        ],
        actions: [
          { description: "Check hard-hat fit and fit chin straps where tasks involve bending, climbing or overhead work.", control_level: "ppe", suggested_owner_role: "Site supervisor" },
          { description: "Review the cause of the impact on site and remove the source if it is still present.", control_level: "elimination", suggested_owner_role: "Site superintendent" },
        ],
      };
    default:
      return {
        hazard: { category: "ppe", energy_source: "Not applicable (PPE use record)" },
        immediate: [{ kind: "substandard_act", description: "The hard hat was removed while the device was assigned to a worker." }],
        whys: ["The hat was taken off.", "The reason is not recorded."],
        actions: [{ description: "Confirm with the worker whether hat removal was intended (for example, in a designated safe area) and remind the crew of head-protection requirements.", control_level: "administrative", suggested_owner_role: "Site supervisor" }],
      };
  }
}

function riskFor(ctx: EventContext): { s: number; l: number; sj: string; lj: string } {
  const m = ctx.event.metrics;
  switch (ctx.event.type) {
    case "struck_by_near_miss":
      return {
        s: (m.closing_rate_mps ?? 0) >= 4 ? 4 : 3,
        l: 3,
        sj: `A strike from behind at about ${f(m.closing_rate_mps)} m/s could cause a serious head, neck or spine injury.`,
        lj: "No contact occurred, but the sensors show a real approach on a shared work path, so a repeat is plausible.",
      };
    case "struck_by_contact":
      return { s: 4, l: 4, sj: "Contact with the hat after an approach at speed can cause serious head or neck injury.", lj: "Contact actually occurred, so the exposure is demonstrated rather than hypothetical." };
    case "head_impact":
      return {
        s: m.peak_g_saturated ? 5 : 4,
        l: 4,
        sj: m.peak_g_saturated ? "The impact reached the sensor limit (at least 16 g), so the true force may have been higher; a serious head injury is credible." : `A head impact of ${f(m.peak_g)} g can cause a concussion or a more serious injury.`,
        lj: "The impact occurred; similar exposure exists for others working in the same area.",
      };
    case "fall":
      return { s: (m.drop_height_m ?? 0) >= 2 ? 5 : 4, l: 3, sj: `A fall with an estimated drop of ${f(m.drop_height_m, 2)} m followed by an impact can cause serious injury.`, lj: "Fall conditions at the location are not fully known; the fall itself shows the hazard was not controlled." };
    case "hat_knocked_off":
      return { s: 3, l: 3, sj: "A hat knocked off by an impact leaves the head unprotected and signals a significant force.", lj: "Hat retention depends on fit and task; a repeat is plausible." };
    default:
      return { s: 2, l: 3, sj: "Short-term exposure without head protection; injury depends on the surrounding hazards.", lj: "Hat removal is common but depends on site rules and context." };
  }
}

function summaryFor(ctx: EventContext): string {
  const ev = ctx.event;
  const m = ev.metrics;
  const who = ctx.workerName;
  switch (ev.type) {
    case "struck_by_near_miss":
      return `The hat's rear sensor ${m.sensor_id ?? ""} detected an object closing on ${who} at ${f(m.closing_rate_mps)} m/s with about ${f(m.ttc_s)} s to contact, and a haptic warning fired. No impact was recorded, so the device classed this as a near miss.`.replace("sensor  ", "sensor ");
    case "struck_by_contact":
      return `An object approached ${who} from behind at about ${f(m.closing_rate_mps)} m/s and an impact of ${f(m.peak_g)} g followed. The device classed this as an incident because contact occurred.`;
    case "head_impact":
      return `${who}'s hard hat registered a head impact${m.peak_g_saturated ? " at or above the 16 g sensor limit" : ` of ${f(m.peak_g)} g`}. The device classed this as an incident; the source of the impact is not identified by the sensors alone.`;
    case "fall":
      return `${who}'s hard hat recorded a free fall of ${f(m.free_fall_ms, 0)} ms (an estimated ${f(m.drop_height_m, 2)} m) followed by an impact of ${f(m.peak_g)} g. The device classed this as an incident.`;
    case "hat_knocked_off":
      return `${who}'s hard hat registered an impact of ${f(m.peak_g)} g and then left the head. The device classed this as an incident.`;
    default:
      return `${who}'s hard hat was taken off during the shift. This is logged as a PPE record; no impact was detected.`;
  }
}

/** Deterministic, schema-valid analysis fields built from the event and trace. No network, no LLM. */
export function buildFixtureDraft(ctx: EventContext, trace: SensorTrace | null): LlmFields {
  const ev = ctx.event;
  const tpl = template(ev.type, ctx);
  const r = riskFor(ctx);
  const regulatory =
    ev.device_class === "incident"
      ? "This event may meet a provincial serious-incident or dangerous-occurrence notification threshold, depending on any injury and on jurisdiction. This draft does not determine whether it does; verify with your provincial OHS regulator."
      : ev.device_class === "near_miss"
        ? "A near miss without injury is usually an internal report, but it may still be a dangerous occurrence in some jurisdictions. Verify with your provincial OHS regulator."
        : "A PPE use record is not normally reportable by itself. Verify with your provincial OHS regulator if an injury or dangerous occurrence is associated with it.";
  const clipSeen = ctx.clip !== null;
  return {
    summary: summaryFor(ctx),
    sequence_of_events: sensorTimeline(ctx, trace).map((t) => ({ offset_s: t.offset_s, description: t.text, source: "sensor" as const })),
    evidence: {
      clip_refs: clipSeen
        ? [{ offset_s: 0, observation: `Review the front-camera clip around t0 (${fmtOffset(0)}); video was not analyzed in fixture mode.` }]
        : [],
      key_sensor_values: keySensorValues(ctx),
      environment: environmentText(ctx),
    },
    hazard: tpl.hazard,
    risk: {
      severity: r.s,
      likelihood: r.l,
      score: r.s * r.l,
      severity_justification: r.sj,
      likelihood_justification: r.lj,
    },
    causes: { immediate: tpl.immediate, root_five_whys: tpl.whys },
    corrective_actions: tpl.actions,
    regulatory_note: regulatory,
    confidence_and_gaps: `Fixture mode: this draft is built from the sensor data only and the video was not analyzed, so visual causes, the activity being performed and nearby people are unknown. ${ev.metrics.peak_g_saturated ? "The peak acceleration was clipped at the sensor limit. " : ""}Location on site is not determined.`,
    classification_review: { suggested_class: null, reason: null },
  };
}
