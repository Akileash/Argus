import { describe, expect, it } from "vitest";
import { SensorTraceSchema } from "@argus/shared";
import { buildTrace, type EventRow } from "../scripts/dev-attach-media";

const base: EventRow = {
  id: "00000000-0000-4000-8000-000000000001",
  type: "struck_by_near_miss",
  window_pre_s: 20,
  window_post_s: 10,
  peak_g: null,
  peak_g_saturated: false,
  closing_rate_mps: 3.4,
  ttc_s: 0.7,
  range_m: 2.38,
  sensor_id: "R3",
  free_fall_ms: null,
  drop_height_m: null,
  co2_ppm: 572,
  temp_c: 7,
  rh_pct: 49,
};

const maxOf = (xs: number[]) => xs.reduce((a, b) => Math.max(a, b), -Infinity);

describe("seed media traces agree with their event row", () => {
  it("struck-by: only the MVP sensors, the triggering one sees the object, range and closing match the row", () => {
    const tr = buildTrace(base);
    expect(SensorTraceSchema.parse(tr)).toBeTruthy();
    expect(Object.keys(tr.ranges).sort()).toEqual(["R2", "R3"]);
    expect(tr.ranges.R2!.range_m.every((v) => v === null)).toBe(true);
    const r3 = tr.ranges.R3!;
    const i0 = r3.t.findIndex((x) => x >= 0);
    expect(r3.range_m[i0]).toBeCloseTo(2.38 - 3.4 * r3.t[i0]!, 1); // range(t) = range_m - closing * t
    expect(r3.closing_mps[i0]).toBeCloseTo(3.4, 0);
    const warns = tr.detections.filter((d) => d.type === "struck_by_warning");
    expect(warns.length).toBeGreaterThan(3);
    expect(warns.every((d) => d.sensor_id === "R3")).toBe(true);
    expect(tr.detections.filter((d) => d.type === "struck_by_near_miss")).toHaveLength(1);
    expect(tr.detections.some((d) => d.type === "head_impact")).toBe(false);
    expect(maxOf(tr.imu.a_mag_g)).toBeLessThan(1.2); // no impact on a near miss
    expect(tr.env).toEqual({ co2_ppm: 572, temp_c: 7, rh_pct: 49 });
    expect(JSON.stringify(tr)).not.toMatch(/"(R1|R4|TOP)"/);
  });

  it("contact: impact lands after the approach with the row's peak g", () => {
    const tr = buildTrace({ ...base, type: "struck_by_contact", peak_g: 5.1, ttc_s: null });
    const hi = tr.detections.find((d) => d.type === "head_impact")!;
    expect(hi.t).toBeGreaterThan(0);
    expect(hi.peak_g).toBeCloseTo(5.1, 1);
    expect(maxOf(tr.imu.a_mag_g)).toBeGreaterThan(4);
    expect(maxOf(tr.imu.a_mag_g)).toBeLessThanOrEqual(5.1);
    expect(tr.detections.some((d) => d.type === "struck_by_contact")).toBe(true);
  });

  it("fall: free fall lasts the row's free_fall_ms, then the impact at t0", () => {
    const tr = buildTrace({ ...base, type: "fall", peak_g: 12.1, free_fall_ms: 410, drop_height_m: 0.82, sensor_id: null, closing_rate_mps: null, ttc_s: null, range_m: null });
    const ff = tr.imu.t.map((t, i) => [t, tr.imu.a_mag_g[i]!] as const).filter(([t, a]) => t >= -0.41 && t < 0 && a < 0.2);
    expect(ff.length).toBeGreaterThanOrEqual(38);
    expect(ff.length).toBeLessThanOrEqual(42);
    const fall = tr.detections.find((d) => d.type === "fall")!;
    expect(fall).toMatchObject({ t: 0, free_fall_ms: 410, drop_height_m: 0.82, peak_g: 12.1 });
    expect(Object.values(tr.ranges).every((r) => r.range_m.every((v) => v === null))).toBe(true);
  });

  it("a saturated peak is flagged as saturated, never an exact value above the 16 g limit", () => {
    const tr = buildTrace({ ...base, type: "head_impact", peak_g: 16, peak_g_saturated: true, sensor_id: null, closing_rate_mps: null, ttc_s: null, range_m: null });
    expect(tr.imu_peaks!.some((p) => p.saturated)).toBe(true);
    expect(maxOf(tr.imu.a_mag_g)).toBeLessThanOrEqual(16);
    expect(tr.detections.find((d) => d.type === "head_impact")).toMatchObject({ peak_g_saturated: true });
  });

  it("is deterministic", () => {
    expect(JSON.stringify(buildTrace(base))).toBe(JSON.stringify(buildTrace(base)));
  });
});
