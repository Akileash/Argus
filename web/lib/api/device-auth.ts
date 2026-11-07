import { createHash, randomBytes, timingSafeEqual } from "node:crypto";
import type { SupabaseClient } from "@supabase/supabase-js";
import { ApiError } from "./errors";

export interface AuthedDevice {
  id: string;
  serial: string;
  org_id: string;
  site_id: string | null;
}

export function sha256Hex(s: string): string {
  return createHash("sha256").update(s).digest("hex");
}

/** 32 random bytes, base64url (43 chars). Shown once; only sha256 is stored. */
export function generateDeviceToken(): string {
  return randomBytes(32).toString("base64url");
}

export function safeEqualHex(a: string, b: string): boolean {
  const ba = Buffer.from(a, "utf8");
  const bb = Buffer.from(b, "utf8");
  return ba.length === bb.length && timingSafeEqual(ba, bb);
}

export function parseBearer(header: string | null): string | null {
  if (!header) return null;
  const m = /^Bearer\s+(\S+)$/i.exec(header.trim());
  return m ? m[1] : null;
}

/** Resolve the device behind an `Authorization: Bearer <token>` header. Throws ApiError 401. */
export async function authenticateDevice(db: SupabaseClient, request: Request): Promise<AuthedDevice> {
  const token = parseBearer(request.headers.get("authorization"));
  if (!token) throw new ApiError(401, "unauthorized", "Missing bearer token");
  const hash = sha256Hex(token);
  const { data, error } = await db
    .from("devices")
    .select("id, serial, org_id, site_id, token_hash")
    .eq("token_hash", hash)
    .maybeSingle();
  if (error) throw new ApiError(500, "internal", error.message);
  if (!data || !data.token_hash || !safeEqualHex(data.token_hash, hash)) {
    throw new ApiError(401, "unauthorized", "Invalid device token");
  }
  return { id: data.id, serial: data.serial, org_id: data.org_id, site_id: data.site_id };
}
