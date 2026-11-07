import { describe, expect, it } from "vitest";
import { assignWorker } from "@/lib/api/assign";
import { isLoopbackUrl, pairCommand } from "@/lib/pairing";
import { FakeDb } from "./helpers/fake-db";

function setup() {
  const db = new FakeDb();
  db.tables.devices.push({ id: "d1", serial: "ARG-0001", org_id: "org-1" }, { id: "d2", serial: "ARG-0002", org_id: "org-1" });
  db.tables.workers = [
    { id: "w1", org_id: "org-1", full_name: "Jordan Okafor" },
    { id: "w2", org_id: "org-1", full_name: "Casey Lindqvist" },
    { id: "w9", org_id: "org-9", full_name: "Other Org" },
  ];
  db.tables.device_assignments = [{ id: "a1", org_id: "org-1", device_id: "d2", worker_id: "w2", starts_at: "2026-01-01T00:00:00.000Z", ends_at: null }];
  return db;
}
const open = (db: FakeDb, device: string) => db.tables.device_assignments.filter((a) => a.device_id === device && a.ends_at == null);
const NOW = new Date("2026-11-07T18:00:00.000Z");

describe("assignWorker", () => {
  it("opens a new assignment for an unassigned device", async () => {
    const db = setup();
    await assignWorker(db.asClient(), { deviceId: "d1", workerId: "w1", orgId: "org-1", now: NOW });
    expect(open(db, "d1")).toHaveLength(1);
    expect(open(db, "d1")[0]).toMatchObject({ worker_id: "w1", org_id: "org-1", starts_at: NOW.toISOString() });
  });

  it("closing the old assignment and opening the new one leaves exactly one open row and keeps history", async () => {
    const db = setup();
    await assignWorker(db.asClient(), { deviceId: "d2", workerId: "w1", orgId: "org-1", now: NOW });
    expect(open(db, "d2")).toHaveLength(1);
    expect(open(db, "d2")[0]!.worker_id).toBe("w1");
    const old = db.tables.device_assignments.find((a) => a.id === "a1")!;
    expect(old.ends_at).toBe(NOW.toISOString());
    expect(db.tables.device_assignments).toHaveLength(2);
  });

  it("unassign closes the open row and opens nothing", async () => {
    const db = setup();
    await assignWorker(db.asClient(), { deviceId: "d2", workerId: null, orgId: "org-1", now: NOW });
    expect(open(db, "d2")).toHaveLength(0);
    expect(db.tables.device_assignments).toHaveLength(1);
  });

  it("is a no-op when the worker is already assigned", async () => {
    const db = setup();
    await assignWorker(db.asClient(), { deviceId: "d2", workerId: "w2", orgId: "org-1", now: NOW });
    expect(db.tables.device_assignments).toHaveLength(1);
    expect(db.tables.device_assignments[0]!.ends_at).toBeNull();
  });

  it("refuses a worker who already wears another hat, and leaves the target device untouched", async () => {
    const db = setup();
    db.tables.device_assignments.push({ id: "a0", org_id: "org-1", device_id: "d1", worker_id: "w1", starts_at: "2026-01-01T00:00:00.000Z", ends_at: null });
    await expect(assignWorker(db.asClient(), { deviceId: "d2", workerId: "w1", orgId: "org-1", now: NOW })).rejects.toMatchObject({ status: 409 });
    expect(open(db, "d2")[0]!.worker_id).toBe("w2");
  });

  it("restores the old assignment if opening the new one fails", async () => {
    const db = setup();
    const client = db.asClient();
    const realFrom = db.from.bind(db);
    db.from = ((table: string) => {
      const q = realFrom(table) as unknown as { insert: (p: unknown) => unknown };
      if (table === "device_assignments") {
        q.insert = () => ({ then: (ok: (v: unknown) => unknown) => Promise.resolve({ data: null, error: { message: "boom" } }).then(ok) });
      }
      return q;
    }) as typeof db.from;
    await expect(assignWorker(client, { deviceId: "d2", workerId: "w1", orgId: "org-1", now: NOW })).rejects.toMatchObject({ status: 409 });
    expect(open(db, "d2")[0]).toMatchObject({ id: "a1", worker_id: "w2" });
  });

  it("refuses unknown devices and workers from another organization", async () => {
    const db = setup();
    await expect(assignWorker(db.asClient(), { deviceId: "nope", workerId: "w1", orgId: "org-1", now: NOW })).rejects.toMatchObject({ status: 404 });
    await expect(assignWorker(db.asClient(), { deviceId: "d1", workerId: "w9", orgId: "org-1", now: NOW })).rejects.toMatchObject({ status: 404 });
  });
});

describe("pairing command", () => {
  it("is the exact command from docs/CONNECT-THE-HAT.md e2", () => {
    expect(pairCommand("http://192.168.1.20:3000/", "tok_abc")).toBe(
      ".venv/bin/argus-cli pair --url http://192.168.1.20:3000 --token tok_abc --config /etc/argus/config.yaml",
    );
  });

  it("flags loopback dashboard addresses, which the Pi cannot reach", () => {
    expect(isLoopbackUrl("http://localhost:3000")).toBe(true);
    expect(isLoopbackUrl("http://127.0.0.1:3400")).toBe(true);
    expect(isLoopbackUrl("http://192.168.1.20:3000")).toBe(false);
    expect(isLoopbackUrl("https://argus.example.com")).toBe(false);
  });
});
