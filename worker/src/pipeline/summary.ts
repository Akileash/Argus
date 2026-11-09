import type { SensorTrace } from "@argus/shared";
import type { EventContext } from "../store/types";

const f = (n: number | null | undefined, d = 1): string => (n === null || n === undefined ? "n/a" : n.toFixed(d));

export function fmtOffset(s: number): string {
  return `${s >= 0 ? "+" : ""}${s.toFixed(1)}s`;
}

/** Sensor timeline entries derived from detections, hat-on changes, haptics and merged triggers. */
export interface TimelineItem {
  offset_s: number;
  text: string;
}

export const SATURATION_G = 16;

/** Peak g for prose. A saturated reading is a lower bound, never an exact number (spec 2.6). */
export function fmtPeakG(peakG: number | null | undefined, saturated: boolean | null | undefined): string {
  if (saturated) return `\u2265 ${SATURATION_G} g (saturated)`;
  return peakG == null ? "n/a" : `${f(peakG)} g`;
}

const MOTOR_NAME: Record<string, string> = { back: "back motor", top: "top motor", both: "both motors" };
const motorName = (m: string): string => MOTOR_NAME[m] ?? `${m} motor`;

/** Join "a, b and c" skipping empty parts. */
function list(parts: Array<string | null>): string {
  const p = parts.filter((x): x is string => !!x);
  return p.length <= 1 ? (p[0] ?? "") : `${p.slice(0, -1).join(", ")} and ${p[p.length - 1]}`;
}

/**
 * Field names are the ones the device really writes (device/argus/events/trace.py): closing_rate_mps,
 * free_fall_ms, peak_g_saturated, drop_height_m; detection types are struck_by_warning, struck_by_near_miss,
 * struck_by_contact, head_impact, fall, hat_knocked_off, hat_removed, hat_on.
 */
export function sensorTimeline(ctx: EventContext, trace: SensorTrace | null): TimelineItem[] {
  const m = ctx.event.metrics;
  const items: TimelineItem[] = [];
  const dets = trace?.detections ?? [];
  const num = (x: Record<string, unknown>, k: string): number | null => (typeof x[k] === "number" ? (x[k] as number) : null);
  const approach = (x: Record<string, unknown>): string =>
    list([
      num(x, "closing_rate_mps") != null ? `closing at ${f(num(x, "closing_rate_mps"))} m/s` : null,
      num(x, "ttc_s") != null ? `about ${f(num(x, "ttc_s"))} s to contact` : null,
      num(x, "range_m") != null ? `${f(num(x, "range_m"), 2)} m away` : null,
    ]);

  // struck_by_warning repeats every sample while an object approaches: one line per sensor, first to last.
  const warnings = new Map<string, Array<{ t: number; x: Record<string, unknown> }>>();
  let sawHatOff = false;
  let sawHatOn = false;
  for (const d of dets) {
    const x = d as Record<string, unknown>;
    const peak = num(x, "peak_g") ?? m.peak_g ?? null;
    const sat = x.peak_g_saturated === true || (num(x, "peak_g") == null && m.peak_g_saturated === true);
    switch (d.type) {
      case "struck_by_warning": {
        const sid = (x.sensor_id as string | null | undefined) ?? m.sensor_id ?? "rear";
        (warnings.get(sid) ?? warnings.set(sid, []).get(sid)!).push({ t: d.t, x });
        break;
      }
      case "struck_by_near_miss": {
        const sid = (x.sensor_id as string | null | undefined) ?? m.sensor_id;
        items.push({
          offset_s: d.t,
          text: `Near miss confirmed${sid ? ` on rear sensor ${sid}` : ""}: an object was ${approach({ ...x, closing_rate_mps: num(x, "closing_rate_mps") ?? m.closing_rate_mps, ttc_s: num(x, "ttc_s") ?? m.ttc_s, range_m: num(x, "range_m") ?? m.range_m }) || "approaching"} with no impact detected.`,
        });
        break;
      }
      case "struck_by_contact":
        items.push({
          offset_s: d.t,
          text: `Contact confirmed: an impact (${fmtPeakG(peak, sat)}) followed the rear approach, so the near miss was upgraded to an incident.`,
        });
        break;
      case "head_impact":
        items.push({ offset_s: d.t, text: `Head impact detected, peak ${fmtPeakG(peak, sat)}.` });
        break;
      case "fall": {
        const ms = num(x, "free_fall_ms") ?? m.free_fall_ms ?? null;
        const h = num(x, "drop_height_m") ?? m.drop_height_m ?? null;
        items.push({
          offset_s: d.t,
          text: `Fall detected: ${ms != null ? `${f(ms, 0)} ms of free fall` : "free fall"}${h != null ? ` (estimated drop ${f(h, 2)} m)` : ""} ending in an impact of ${fmtPeakG(peak, sat)}.`,
        });
        break;
      }
      case "hat_knocked_off":
        sawHatOff = true;
        items.push({ offset_s: d.t, text: `The hat was knocked off the head after an impact of ${fmtPeakG(peak, sat)}.` });
        break;
      case "hat_removed":
        sawHatOff = true;
        items.push({ offset_s: d.t, text: "The hat was taken off the head (hat-on sensor changed to off)." });
        break;
      case "hat_on":
        sawHatOn = true;
        items.push({ offset_s: d.t, text: "The hat was put back on (hat-on sensor changed to on)." });
        break;
      default:
        items.push({ offset_s: d.t, text: `Device logged a ${d.type.replaceAll("_", " ")} detection.` });
    }
  }
  for (const [sid, ws] of warnings) {
    const first = ws[0]!;
    const last = ws[ws.length - 1]!;
    const first0 = approach(first.x);
    if (ws.length === 1) {
      items.push({ offset_s: first.t, text: `Rear sensor ${sid} warned of an approaching object${first0 ? ` (${first0})` : ""}.` });
    } else {
      const lastRange = num(last.x, "range_m");
      items.push({
        offset_s: first.t,
        text: `Rear sensor ${sid} warned of an approaching object ${ws.length} times between ${fmtOffset(first.t)} and ${fmtOffset(last.t)}; first reading ${first0 || "n/a"}${lastRange != null ? `, last reading ${f(lastRange, 2)} m away` : ""}.`,
      });
    }
  }
  if (!sawHatOff || !sawHatOn) {
    for (const h of trace?.hat_on ?? []) {
      if (h.t <= (trace?.t_start_s ?? -Infinity)) continue;
      if (!h.on && !sawHatOff) items.push({ offset_s: h.t, text: "The hat was taken off the head (hat-on sensor changed to off)." });
      if (h.on && !sawHatOn) items.push({ offset_s: h.t, text: "The hat was put back on (hat-on sensor changed to on)." });
    }
  }

  // Haptics: warning pulses repeat several times a second, so group them per motor into one line.
  const pulses = new Map<string, Array<{ t: number; duration_s: number }>>();
  for (const h of trace?.haptics ?? []) {
    if (/^pulse/.test(h.pattern)) {
      (pulses.get(h.motor) ?? pulses.set(h.motor, []).get(h.motor)!).push(h);
    } else if (/^ack/.test(h.pattern)) {
      items.push({ offset_s: h.t, text: `Confirmation buzz on ${motorName(h.motor)} (${f(h.duration_s)} s) to tell the wearer the event was recorded.` });
    } else if (h.pattern === "continuous") {
      items.push({ offset_s: h.t, text: `Continuous buzz on the ${motorName(h.motor)} (${f(h.duration_s)} s).` });
    } else {
      items.push({ offset_s: h.t, text: `Haptic alert on ${motorName(h.motor)} (${h.pattern.replaceAll("_", " ")}, ${f(h.duration_s)} s).` });
    }
  }
  for (const [motor, ps] of pulses) {
    const first = ps[0]!;
    const last = ps[ps.length - 1]!;
    items.push({
      offset_s: first.t,
      text:
        ps.length === 1
          ? `Warning pulse on the ${motorName(motor)}.`
          : `Warning pulses on the ${motorName(motor)}: ${ps.length} bursts from ${fmtOffset(first.t)} to ${fmtOffset(last.t + last.duration_s)}.`,
    });
  }

  for (const mt of ctx.event.merged_triggers ?? []) {
    if (ctx.event.type === "fall" && mt.type === "struck_by_near_miss") {
      items.push({
        offset_s: mt.offset_s,
        text: `A struck-by near miss was detected: an object closed on the back of the worker's head ${Math.abs(mt.offset_s).toFixed(1)} s before this fall began. Given the timing, being struck by or reacting to that object is the most likely cause of the fall. This is an inference: the hat has no contact sensor, so contact and any injury are not confirmed.`,
      });
      continue;
    }
    items.push({ offset_s: mt.offset_s, text: `A further trigger (${mt.type.replaceAll("_", " ")}) was merged into this event.` });
  }
  {
    const mods = ctx.event.firmware?.fallbacks_active ?? [];
    if (mods.length) items.push({ offset_s: 0, text: `Module fallback active: ${mods.join(", ")} switched to its backup implementation.` });
  }
  if (items.length === 0) {
    items.push({ offset_s: 0, text: `Device recorded a ${ctx.event.type.replaceAll("_", " ")} event at the trigger time.` });
  }
  return items.sort((a, b) => a.offset_s - b.offset_s);
}

export function environmentText(ctx: EventContext): string {
  const e = ctx.event.env;
  const parts: string[] = [];
  if (e?.co2_ppm != null) parts.push(`CO2 ${f(e.co2_ppm, 0)} ppm`);
  if (e?.temp_c != null) parts.push(`temperature ${f(e.temp_c)} C`);
  if (e?.rh_pct != null) parts.push(`relative humidity ${f(e.rh_pct, 0)}%`);
  if (parts.length === 0) return "No environmental readings were recorded for this event.";
  return `Near-wearer conditions (indicative; the sensor is biased by the wearer's breath and head heat): ${parts.join(", ")}.`;
}

export function keySensorValues(ctx: EventContext): Array<{ name: string; value: string }> {
  const m = ctx.event.metrics;
  const out: Array<{ name: string; value: string }> = [];
  if (m.peak_g != null) {
    out.push({
      name: "Peak acceleration",
      value: fmtPeakG(m.peak_g, m.peak_g_saturated),
    });
  }
  if (m.closing_rate_mps != null) out.push({ name: "Closing rate", value: `${f(m.closing_rate_mps)} m/s` });
  if (m.ttc_s != null) out.push({ name: "Time to contact", value: `${f(m.ttc_s)} s` });
  if (m.range_m != null) out.push({ name: "Range at trigger", value: `${f(m.range_m, 2)} m` });
  if (m.sensor_id) out.push({ name: "Triggering sensor", value: m.sensor_id });
  if (m.free_fall_ms != null) out.push({ name: "Free-fall duration", value: `${f(m.free_fall_ms, 0)} ms` });
  if (m.drop_height_m != null) out.push({ name: "Estimated drop height", value: `${f(m.drop_height_m, 2)} m` });
  out.push({ name: "Hat on at trigger", value: m.hat_on ? "yes" : "no" });
  return out;
}

/** Text block describing the event + sensor data for the LLM prompts. */
export function eventPromptText(ctx: EventContext, trace: SensorTrace | null): string {
  const ev = ctx.event;
  const lines: string[] = [];
  lines.push(`Event type: ${ev.type}`);
  lines.push(`Device class (fixed, set by the device): ${ev.device_class}`);
  lines.push(`Trigger time t0 (UTC): ${ev.t0}`);
  lines.push(`Clip window: ${ev.window.pre_s} s before t0 to ${ev.window.post_s} s after t0 (the clip starts at offset -${ev.window.pre_s} s).`);
  lines.push(`Worker: ${ctx.workerName}; crew: ${ctx.crewName}; supervisor: ${ctx.supervisorName}; site: ${ctx.siteName}; device: ${ctx.deviceSerial}.`);
  lines.push("");
  lines.push("Key sensor values:");
  for (const kv of keySensorValues(ctx)) lines.push(`- ${kv.name}: ${kv.value}`);
  lines.push("");
  lines.push(`Environment: ${environmentText(ctx)}`);
  lines.push("");
  lines.push("Sensor timeline (seconds relative to t0):");
  for (const it of sensorTimeline(ctx, trace)) lines.push(`- ${fmtOffset(it.offset_s)}: ${it.text}`);
  if (trace) {
    const peaks = (trace.imu_peaks ?? []).filter((p) => p.a_mag_g > 3).slice(0, 10);
    if (peaks.length) {
      lines.push("");
      lines.push("Full-rate acceleration peaks above 3 g:");
      for (const p of peaks) lines.push(`- ${fmtOffset(p.t)}: ${fmtPeakG(p.a_mag_g, p.saturated)}`);
    }
    for (const [sid, r] of Object.entries(trace.ranges)) {
      const valid = r.range_m.map((v, i) => [v, r.closing_mps[i], r.t[i]] as const).filter(([v]) => v !== null);
      if (valid.length) {
        const min = valid.reduce((a, b) => (b[0]! < a[0]! ? b : a));
        lines.push(`Range sensor ${sid}: ${valid.length} valid echoes; closest ${f(min[0], 2)} m at ${fmtOffset(min[2]!)}.`);
      }
    }
  } else {
    lines.push("(No sensor trace file was available; only the event metrics above.)");
  }
  return lines.join("\n");
}
