import { describe, expect, it } from "vitest";
import { authenticateDevice, generateDeviceToken, parseBearer, safeEqualHex, sha256Hex } from "@/lib/api/device-auth";
import { ApiError } from "@/lib/api/errors";
import { FakeDb } from "./helpers/fake-db";

function req(auth?: string) {
  return new Request("http://x/api/devices/me", { headers: auth ? { authorization: auth } : {} });
}

describe("device token auth", () => {
  it("generates 32-byte url-safe tokens", () => {
    const t = generateDeviceToken();
    expect(Buffer.from(t, "base64url")).toHaveLength(32);
    expect(generateDeviceToken()).not.toBe(t);
  });

  it("parses bearer headers", () => {
    expect(parseBearer("Bearer abc")).toBe("abc");
    expect(parseBearer("bearer abc")).toBe("abc");
    expect(parseBearer("Basic abc")).toBeNull();
    expect(parseBearer(null)).toBeNull();
  });

  it("constant-time compare handles length mismatch", () => {
    expect(safeEqualHex("aa", "aa")).toBe(true);
    expect(safeEqualHex("aa", "ab")).toBe(false);
    expect(safeEqualHex("aa", "aaaa")).toBe(false);
  });

  it("authenticates by sha256 of the token", async () => {
    const db = new FakeDb();
    db.tables.devices.push({ id: "d1", serial: "ARG-0002", org_id: "o1", site_id: null, token_hash: sha256Hex("tok-123") });
    const d = await authenticateDevice(db.asClient(), req("Bearer tok-123"));
    expect(d).toEqual({ id: "d1", serial: "ARG-0002", org_id: "o1", site_id: null });
  });

  it("rejects missing, wrong, and unpaired", async () => {
    const db = new FakeDb();
    db.tables.devices.push({ id: "d1", serial: "S", org_id: "o1", site_id: null, token_hash: sha256Hex("good") });
    db.tables.devices.push({ id: "d2", serial: "T", org_id: "o1", site_id: null, token_hash: null });
    for (const h of [undefined, "Bearer nope", "Bearer ", "Token good"]) {
      await expect(authenticateDevice(db.asClient(), req(h))).rejects.toMatchObject({ status: 401 });
    }
    await expect(authenticateDevice(db.asClient(), req("Bearer good"))).resolves.toBeTruthy();
    expect(new ApiError(401, "unauthorized", "x")).toBeInstanceOf(Error);
  });
});
