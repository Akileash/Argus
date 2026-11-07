/** Time mapping between the video playhead (seconds from clip start) and the sensor trace (seconds relative to t0). */

export const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/** Video time -> trace time. The clip starts `preS` seconds before t0. */
export const videoToTrace = (videoT: number, preS: number) => videoT - preS;

/** Trace time -> video time, clamped to the clip. */
export const traceToVideo = (traceT: number, preS: number, durationS: number) =>
  clamp(traceT + preS, 0, Math.max(0, durationS));

export interface Domain {
  min: number;
  max: number;
}

/** Map a trace time to an x pixel within [x0, x1]. */
export function timeToX(t: number, d: Domain, x0: number, x1: number): number {
  if (d.max === d.min) return x0;
  return x0 + ((t - d.min) / (d.max - d.min)) * (x1 - x0);
}

/** Map an x pixel within [x0, x1] back to a trace time, clamped to the domain. */
export function xToTime(x: number, d: Domain, x0: number, x1: number): number {
  if (x1 === x0) return d.min;
  const f = clamp((x - x0) / (x1 - x0), 0, 1);
  return d.min + f * (d.max - d.min);
}

/** Domain of the evidence window: [-pre, +post] around t0. */
export function windowDomain(preS: number, postS: number): Domain {
  return { min: -preS, max: postS };
}

/** Format a trace time as "+0.35 s" / "-1.20 s". */
export function fmtOffset(t: number, digits = 2): string {
  return `${t >= 0 ? "+" : "−"}${Math.abs(t).toFixed(digits)} s`;
}
