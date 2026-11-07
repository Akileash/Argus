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

export function sensorTimeline(ctx: EventContext, trace: SensorTrace | null): TimelineItem[] {
  const m = ctx.event.metrics;
  const items: TimelineItem[] = [];
  const dets = trace?.detections ?? [];
  let sawHatOff = false;
  for (const d of dets) {
    const x = d as Record<string, unknown>;
    const num = (k: string): number | null => (typeof x[k] === "number" ? (x[k] as number) : null);
    switch (d.type) {
      case "struck_by_warning": {
        const sensor = (x.sensor_id as string | undefined) ?? m.sensor_id ?? "a rear sensor";
        const closing = num("closing_mps") ?? m.closing_rate_mps ?? null;
        const ttc = num("ttc_s") ?? m.ttc_s ?? null;
        const range = num("range_m") ?? m.range_m ?? null;
        items.push({
          offset_s: d.t,
          text: `Range sensor ${sensor} flagged an object approaching the hat (closing rate ${f(closing)} m/s, time to contact ${f(ttc)} s, range ${f(range)} m).`,
        });
        break;
      }
      case "head_impact": {
        const pk = num("peak_g") ?? m.peak_g ?? null;
        const sat = x.saturated === true || m.peak_g_saturated === true;
        items.push({
          offset_s: d.t,
          text: sat
            ? `Head impact detected; acceleration reached the sensor limit (at least 16 g, saturated, so the exact peak is unknown).`
            : `Head impact detected with a peak of ${f(pk)} g.`,
        });
        break;
      }
      case "free_fall": {
        const ms = num("duration_ms") ?? m.free_fall_ms ?? null;
        const h = m.drop_height_m ?? null;
        items.push({
          offset_s: d.t,
          text: `Free fall detected for ${f(ms, 0)} ms${h !== null ? ` (estimated drop height ${f(h, 2)} m)` : ""}.`,
        });
        break;
      }
      case "hat_off":
      case "hat_removed":
        sawHatOff = true;
        items.push({ offset_s: d.t, text: "The hat-on sensor changed to off: the hat left the wearer's head." });
        break;
      case "trigger":
        items.push({ offset_s: d.t, text: `Event trigger recorded${typeof x.reason === "string" ? ` (${x.reason})` : ""}.` });
        break;
      default:
        items.push({ offset_s: d.t, text: `Detector recorded "${d.type}".` });
    }
  }
  if (!sawHatOff) {
    for (const h of trace?.hat_on ?? []) {
      if (h.t > (trace?.t_start_s ?? -Infinity) && !h.on) {
        items.push({ offset_s: h.t, text: "The hat-on sensor changed to off: the hat left the wearer's head." });
      }
    }
  }
  for (const h of trace?.haptics ?? []) {
    items.push({ offset_s: h.t, text: `Haptic alert fired (${h.motor} motor, ${h.pattern}, ${f(h.duration_s)} s).` });
  }
  for (const mt of ctx.event.merged_triggers ?? []) {
    items.push({ offset_s: mt.offset_s, text: `A further trigger (${mt.type.replaceAll("_", " ")}) was merged into this event.` });
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
      value: m.peak_g_saturated ? "at least 16 g (sensor saturated)" : `${f(m.peak_g)} g`,
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
      for (const p of peaks) lines.push(`- ${fmtOffset(p.t)}: ${f(p.a_mag_g)} g${p.saturated ? " (saturated)" : ""}`);
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
