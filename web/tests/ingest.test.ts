import { beforeEach, describe, expect, it } from "vitest";
import { completeEvent, ingestEvent } from "@/lib/api/events";
import { isSafeFilename, mediaPath } from "@/lib/api/storage";
import { FakeDb } from "./helpers/fake-db";
import { DEVICE, EVENT_ID, hatRemovedEvent, nearMissEvent } from "./helpers/fixtures";

let db: FakeDb;
beforeEach(() => {
  db = new FakeDb();
});

describe("storage paths", () => {
  it("builds <org_id>/<event_id>/<filename>", () => {
    expect(mediaPath("org-1", EVENT_ID, "front.mp4")).toBe(`org-1/${EVENT_ID}/front.mp4`);
  });
  it("rejects traversal and separators", () => {
    for (const bad of ["../x", "a/b", "", "..", "a b"]) expect(isSafeFilename(bad)).toBe(false);
    expect(() => mediaPath("o", "e", "../x")).toThrow();
  });
});

describe("ingestEvent", () => {
  it("creates event + media, returns signed URLs at the right paths (201)", async () => {
    const r = await ingestEvent(db.asClient(), DEVICE, nearMissEvent());
    expect(r.httpStatus).toBe(201);
    expect(r.body.status).toBe("uploading");
    expect(r.body.uploads.map((u) => u.filename).sort()).toEqual(["front.mp4", "sensor_trace.json"]);
    expect(db.signed.sort()).toEqual([`org-1/${EVENT_ID}/front.mp4`, `org-1/${EVENT_ID}/sensor_trace.json`]);
    expect(db.tables.events).toHaveLength(1);
    expect(db.tables.media).toHaveLength(2);
    expect(db.tables.events[0]).toMatchObject({ id: EVENT_ID, device_id: "dev-1", device_class: "near_miss", peak_g: 2.1, sensor_id: "R2" });
  });

  it("is idempotent: repeat returns 200, no duplicates, fresh URLs for unconfirmed media only", async () => {
    await ingestEvent(db.asClient(), DEVICE, nearMissEvent());
    db.tables.media.find((m) => m.filename === "front.mp4")!.confirmed = true;
    db.signed.length = 0;
    const r = await ingestEvent(db.asClient(), DEVICE, nearMissEvent());
    expect(r.httpStatus).toBe(200);
    expect(db.tables.events).toHaveLength(1);
    expect(db.tables.media).toHaveLength(2);
    expect(r.body.uploads.map((u) => u.filename)).toEqual(["sensor_trace.json"]);
  });

  it("repeat after completion returns no uploads and current status", async () => {
    await ingestEvent(db.asClient(), DEVICE, nearMissEvent());
    db.tables.events[0].status = "uploaded";
    const r = await ingestEvent(db.asClient(), DEVICE, nearMissEvent());
    expect(r.httpStatus).toBe(200);
    expect(r.body).toMatchObject({ status: "uploaded", uploads: [] });
  });

  it("hat_removed short-circuits to ready with no uploads or media", async () => {
    const r = await ingestEvent(db.asClient(), DEVICE, hatRemovedEvent());
    expect(r.httpStatus).toBe(201);
    expect(r.body).toMatchObject({ status: "ready", uploads: [] });
    expect(db.tables.media).toHaveLength(0);
    expect(db.signed).toHaveLength(0);
  });

  it("module_fallback also short-circuits", async () => {
    const ev = nearMissEvent({ type: "module_fallback", device_class: "system", media: [] });
    const r = await ingestEvent(db.asClient(), DEVICE, ev);
    expect(r.body.status).toBe("ready");
  });

  it("rejects serial mismatch (403) and event_id owned by another device (409)", async () => {
    await expect(ingestEvent(db.asClient(), DEVICE, nearMissEvent({ device_serial: "ARG-9999" }))).rejects.toMatchObject({ status: 403 });
    await ingestEvent(db.asClient(), DEVICE, nearMissEvent());
    await expect(ingestEvent(db.asClient(), { ...DEVICE, id: "dev-2" }, nearMissEvent())).rejects.toMatchObject({ status: 409 });
  });

  it("rejects unsafe or duplicate filenames (422)", async () => {
    const ev = nearMissEvent();
    ev.media[0].filename = "../evil.mp4";
    await expect(ingestEvent(db.asClient(), DEVICE, ev)).rejects.toMatchObject({ status: 422 });
    const dup = nearMissEvent();
    dup.media[1].filename = "front.mp4";
    await expect(ingestEvent(db.asClient(), DEVICE, dup)).rejects.toMatchObject({ status: 422 });
  });
});

describe("completeEvent", () => {
  const folder = `org-1/${EVENT_ID}`;
  async function setup() {
    await ingestEvent(db.asClient(), DEVICE, nearMissEvent());
    db.objects["event-media"] = { [`${folder}/front.mp4`]: 1000, [`${folder}/sensor_trace.json`]: 200 };
  }

  it("confirms media and moves event to uploaded", async () => {
    await setup();
    const r = await completeEvent(db.asClient(), DEVICE, EVENT_ID, ["front.mp4", "sensor_trace.json"]);
    expect(r).toEqual({ status: "uploaded" });
    expect(db.tables.media.every((m) => m.confirmed === true)).toBe(true);
    expect(db.tables.events[0].status).toBe("uploaded");
  });

  it("409 when an object is missing or size differs; nothing confirmed", async () => {
    await setup();
    delete db.objects["event-media"][`${folder}/sensor_trace.json`];
    await expect(completeEvent(db.asClient(), DEVICE, EVENT_ID, ["front.mp4", "sensor_trace.json"])).rejects.toMatchObject({ status: 409 });
    db.objects["event-media"][`${folder}/sensor_trace.json`] = 5;
    await expect(completeEvent(db.asClient(), DEVICE, EVENT_ID, ["front.mp4", "sensor_trace.json"])).rejects.toMatchObject({ status: 409 });
    expect(db.tables.media.some((m) => m.confirmed)).toBe(false);
    expect(db.tables.events[0].status).toBe("uploading");
  });

  it("409 when a media file is not reported as uploaded", async () => {
    await setup();
    await expect(completeEvent(db.asClient(), DEVICE, EVENT_ID, ["front.mp4"])).rejects.toMatchObject({ status: 409 });
  });

  it("is idempotent once uploaded; 404 for unknown/foreign event", async () => {
    await setup();
    await completeEvent(db.asClient(), DEVICE, EVENT_ID, ["front.mp4", "sensor_trace.json"]);
    await expect(completeEvent(db.asClient(), DEVICE, EVENT_ID, [])).resolves.toEqual({ status: "uploaded" });
    await expect(completeEvent(db.asClient(), { ...DEVICE, id: "other" }, EVENT_ID, [])).rejects.toMatchObject({ status: 404 });
    await expect(completeEvent(db.asClient(), DEVICE, "33333333-3333-4333-8333-333333333333", [])).rejects.toMatchObject({ status: 404 });
  });
});
