// Mountain Time. Same rules as Edmonton, but Chromium 158 keeps America/Edmonton on MDT after DST ended
// in Nov 2026 (one hour wrong on stage), while America/Denver is correct in both Chromium and Node.
export const DISPLAY_TZ = "America/Denver";

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: DISPLAY_TZ,
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZoneName: "short",
  }).format(new Date(iso));
}

/** Peak acceleration for display. A saturated reading is a lower bound, never an exact number (spec 2.6). */
export const SATURATION_G = 16;
export function fmtPeakG(peakG: number | null | undefined, saturated: boolean | null | undefined): string {
  if (saturated) return `\u2265 ${SATURATION_G} g (saturated)`;
  return peakG == null ? "\u2014" : `${peakG.toFixed(1)} g`;
}
