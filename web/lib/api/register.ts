import type { SupabaseClient } from "@supabase/supabase-js";
import { generateDeviceToken, sha256Hex } from "./device-auth";
import { ApiError } from "./errors";

export interface RegisterInput {
  serial: unknown;
  orgId: unknown;
  sessionOrgId: string;
  rotate?: boolean;
}

/**
 * Pair a device: set token_hash on an existing unpaired row, or create the device.
 * Already-paired devices are refused (409) unless `rotate` is true. The plaintext token is returned once.
 */
export async function registerDevice(db: SupabaseClient, input: RegisterInput): Promise<{ device_id: string; device_token: string }> {
  const serial = typeof input.serial === "string" ? input.serial.trim() : "";
  if (!/^[A-Za-z0-9._-]{1,64}$/.test(serial)) throw new ApiError(422, "validation_failed", "`serial` is required (letters, digits, . _ -)");
  if (input.orgId !== input.sessionOrgId) throw new ApiError(403, "forbidden", "org_id does not match your organization");
  const orgId = input.sessionOrgId;

  const token = generateDeviceToken();
  const hash = sha256Hex(token);

  const found = await db.from("devices").select("id, org_id, token_hash").eq("serial", serial).maybeSingle();
  if (found.error) throw new ApiError(500, "internal", found.error.message);
  const existing = found.data as { id: string; org_id: string; token_hash: string | null } | null;

  if (existing) {
    if (existing.org_id !== orgId) throw new ApiError(409, "conflict", "Serial belongs to another organization");
    if (existing.token_hash && !input.rotate) {
      throw new ApiError(409, "already_paired", "Device already has a token; pass rotate=true to issue a new one");
    }
    const u = await db.from("devices").update({ token_hash: hash }).eq("id", existing.id);
    if (u.error) throw new ApiError(500, "internal", u.error.message);
    return { device_id: existing.id, device_token: token };
  }

  const site = await db.from("sites").select("id").eq("org_id", orgId).order("created_at").limit(1).maybeSingle();
  const created = await db
    .from("devices")
    .insert({ org_id: orgId, serial, token_hash: hash, site_id: (site.data as { id: string } | null)?.id ?? null })
    .select("id")
    .single();
  if (created.error) throw new ApiError(500, "internal", created.error.message);
  return { device_id: (created.data as { id: string }).id, device_token: token };
}
