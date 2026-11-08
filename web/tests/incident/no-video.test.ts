import { describe, expect, it } from "vitest";
import { NO_VIDEO_NOTE } from "@/lib/report/no-video";
import { stepStates } from "@/components/incident/processing-stepper";

describe("no-video note and failed state", () => {
  it("says what happened in plain words", () => {
    expect(NO_VIDEO_NOTE).toMatch(/Video not analysed/);
    expect(NO_VIDEO_NOTE).toMatch(/sensor data/);
  });
  it("a failed event shows the failing step, not a spinner", () => {
    expect(stepStates("failed", false)).toEqual(["done", "failed", "pending"]);
  });
});
