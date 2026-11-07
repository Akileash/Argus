import { describe, expect, it } from "vitest";
import { sha256Hex } from "@/lib/api/device-auth";
import { registerDevice } from "@/lib/api/register";
import { FakeDb } from "./helpers/fake-db";

describe("registerDevice", () => {
  it("sets the hash on an existing unpaired device and returns the token once", async () => {
    const db = new FakeDb();
    db.tables.devices.push({ id: "d1", serial: "ARG-0001", org_id: "org-1", token_hash: null });
    const r = await registerDevice(db.asClient(), { serial: "ARG-0001", orgId: "org-1", sessionOrgId: "org-1" });
    expect(r.device_id).toBe("d1");
    expect(db.tables.devices[0].token_hash).toBe(sha256Hex(r.device_token));
  });

  it("creates a device when the serial is new", async () => {
    const db = new FakeDb();
    db.tables.sites.push({ id: "s1", org_id: "org-1" });
    const r = await registerDevice(db.asClient(), { serial: "ARG-0100", orgId: "org-1", sessionOrgId: "org-1" });
    expect(db.tables.devices).toHaveLength(1);
    expect(db.tables.devices[0]).toMatchObject({ serial: "ARG-0100", site_id: "s1", token_hash: sha256Hex(r.device_token) });
  });

  it("refuses already-paired unless rotate; refuses foreign org; validates serial", async () => {
    const db = new FakeDb();
    db.tables.devices.push({ id: "d1", serial: "ARG-0002", org_id: "org-1", token_hash: "x" });
    await expect(registerDevice(db.asClient(), { serial: "ARG-0002", orgId: "org-1", sessionOrgId: "org-1" })).rejects.toMatchObject({ status: 409 });
    await expect(registerDevice(db.asClient(), { serial: "ARG-0002", orgId: "org-1", sessionOrgId: "org-1", rotate: true })).resolves.toBeTruthy();
    await expect(registerDevice(db.asClient(), { serial: "ARG-0002", orgId: "org-9", sessionOrgId: "org-1" })).rejects.toMatchObject({ status: 403 });
    await expect(registerDevice(db.asClient(), { serial: "bad serial!", orgId: "org-1", sessionOrgId: "org-1" })).rejects.toMatchObject({ status: 422 });
  });
});
