// Edmonton local time, currently UTC-6 year round ("CST"). Browsers and Node disagree on America/Edmonton
// (their bundled time zone data ranges from -6 to -7), so use America/Regina: fixed UTC-6 everywhere.
export const DISPLAY_TZ = "America/Regina";

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
