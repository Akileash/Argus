import { describe, expect, it } from "vitest";
import { SensorTraceSchema } from "@argus/shared";
import { clamp, fmtOffset, timeToX, traceToVideo, videoToTrace, windowDomain, xToTime } from "@/lib/report/timeline";
import { stepStates } from "@/components/incident/processing-stepper";
import { clusterDetections } from "@/components/incident/sensor-chart";
import { buildTrace } from "../../scripts/dev-attach-media";

describe("playhead <-> chart time mapping", () => {
  it("video time 0 is -pre; t0 sits at video time pre", () => {
    expect(videoToTrace(0, 20)).toBe(-20);
    expect(videoToTrace(20, 20)).toBe(0);
    expect(traceToVideo(0, 20, 30)).toBe(20);
    expect(traceToVideo(-20, 20, 30)).toBe(0);
  });
  it("round-trips and clamps to the clip", () => {
    for (const v of [0, 3.25, 20, 29.9]) expect(traceToVideo(videoToTrace(v, 20), 20, 30)).toBeCloseTo(v, 9);
    expect(traceToVideo(99, 20, 30)).toBe(30);
    expect(traceToVideo(-99, 20, 30)).toBe(0);
  });
  it("maps time to x and back across the plot area", () => {
    const d = windowDomain(20, 10);
    expect(timeToX(-20, d, 46, 1046)).toBe(46);
    expect(timeToX(10, d, 46, 1046)).toBe(1046);
    expect(timeToX(0, d, 46, 1046)).toBeCloseTo(46 + (20 / 30) * 1000, 6);
    for (const t of [-20, -7.5, 0, 4.2, 10]) expect(xToTime(timeToX(t, d, 46, 1046), d, 46, 1046)).toBeCloseTo(t, 9);
  });
  it("clamps pointer positions outside the plot to the domain ends", () => {
    const d = windowDomain(20, 10);
    expect(xToTime(-500, d, 46, 1046)).toBe(-20);
    expect(xToTime(5000, d, 46, 1046)).toBe(10);
    expect(clamp(5, 0, 3)).toBe(3);
  });
  it("formats offsets with sign", () => {
    expect(fmtOffset(0.35)).toBe("+0.35 s");
    expect(fmtOffset(-1.2)).toBe("−1.20 s");
  });
});

describe("processing stepper", () => {
  it("maps statuses to three steps", () => {
    expect(stepStates("received", false)).toEqual(["active", "pending", "pending"]);
    expect(stepStates("uploaded", false)).toEqual(["done", "active", "pending"]);
    expect(stepStates("analyzing", false)).toEqual(["done", "active", "pending"]);
    expect(stepStates("analyzing", true)).toEqual(["done", "done", "active"]);
    expect(stepStates("ready", false)).toEqual(["done", "done", "done"]);
    expect(stepStates("failed", true)).toEqual(["done", "done", "failed"]);
  });
});

describe("dev trace generator", () => {
  it("produces a schema-valid full-window trace with an impact peak", () => {
    const t = buildTrace({
      id: "6f1d2c1a-3b4e-4f5a-8b6c-7d8e9f0a1b2c",
      type: "head_impact",
      window_pre_s: 20,
      window_post_s: 10,
      peak_g: 16,
      peak_g_saturated: true,
      closing_rate_mps: null,
      ttc_s: null,
      range_m: null,
      sensor_id: null,
      free_fall_ms: null,
      drop_height_m: null,
      co2_ppm: null,
      temp_c: null,
      rh_pct: null,
    });
    expect(SensorTraceSchema.safeParse(t).success).toBe(true);
    expect(t.imu.t[0]).toBe(-20);
    expect(t.imu.t.at(-1)).toBe(10);
    expect(t.imu_peaks?.some((p) => p.saturated)).toBe(true);
  });
});

describe("chart detection labels", () => {
  it("collapses the warning burst and the confirmed near miss at t0 into one label", () => {
    const warns = Array.from({ length: 10 }, (_, i) => ({ t: i * 0.06, type: "struck_by_warning" }));
    const out = clusterDetections([...warns, { t: 0, type: "struck_by_near_miss" }], 600 / 30);
    expect(out).toHaveLength(1);
    expect(out[0]!.label).toBe("WARN \u00d710 \u00b7 NEAR MISS");
    expect(out[0]!.times).toHaveLength(3); // first + last warning, and the near miss
  });

  it("keeps well-separated detections as separate labels", () => {
    const out = clusterDetections([{ t: 0, type: "fall" }, { t: 6, type: "hat_removed" }], 20);
    expect(out.map((c) => c.label)).toEqual(["FALL", "HAT OFF"]);
  });
});
