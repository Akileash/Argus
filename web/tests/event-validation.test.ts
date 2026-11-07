import { describe, expect, it } from "vitest";
import { validateEvent } from "@/lib/contracts/event";
import { hatRemovedEvent, nearMissEvent } from "./helpers/fixtures";

describe("event payload validation", () => {
  it("accepts a valid near-miss fixture", () => {
    expect(validateEvent(nearMissEvent()).ok).toBe(true);
  });

  it("accepts hat_removed with empty media", () => {
    expect(validateEvent(hatRemovedEvent()).ok).toBe(true);
  });

  const required = ["schema_version", "event_id", "device_serial", "type", "device_class", "t0", "detected_at", "window", "metrics", "media", "firmware"];
  it.each(required)("rejects a payload missing %s", (field) => {
    const body: Record<string, unknown> = { ...nearMissEvent() };
    delete body[field];
    const r = validateEvent(body);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.errors.some((e) => e.keyword === "required" && (e.params as { missingProperty: string }).missingProperty === field)).toBe(true);
  });

  it("rejects wrong device_class value", () => {
    const r = validateEvent({ ...nearMissEvent(), device_class: "bogus" });
    expect(r.ok).toBe(false);
  });

  it("rejects non-uuid event_id and bad sha256", () => {
    expect(validateEvent({ ...nearMissEvent(), event_id: "not-a-uuid" }).ok).toBe(false);
    const ev = nearMissEvent();
    ev.media[0].sha256 = "xyz";
    expect(validateEvent(ev).ok).toBe(false);
  });

  it("rejects unknown top-level properties (additionalProperties: false)", () => {
    expect(validateEvent({ ...nearMissEvent(), extra: 1 }).ok).toBe(false);
  });

  it("rejects metrics without hat_on", () => {
    const ev = nearMissEvent();
    // @ts-expect-error deliberately invalid
    delete ev.metrics.hat_on;
    expect(validateEvent(ev).ok).toBe(false);
  });
});
