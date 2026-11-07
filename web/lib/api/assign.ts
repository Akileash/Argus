import type { SupabaseClient } from "@supabase/supabase-js";
import { ApiError } from "./errors";

export interface AssignInput {
  deviceId: string;
  /** null = unassign (close the open assignment, open nothing). */
  workerId: string | null;
  orgId: string;
  now?: Date;
}

const dbErr = (e: { message: string } | null, what: string) => {
  if (e) throw new ApiError(500, "internal", `${what}: ${e.message}`);
};

/**
 * Give a device to a worker: close the device's open assignment and open a new one at the same instant.
 * The schema allows at most one open assignment per device AND per worker (partial unique indexes), so a worker
 * who already wears another hat is refused up front. Everything runs as the signed-in manager, so RLS
 * (manager + same org) is what authorises the writes. If opening the new row fails, the old one is reopened.
 */
export async function assignWorker(db: SupabaseClient, input: AssignInput): Promise<{ assigned_worker_id: string | null }> {
  const { deviceId, workerId, orgId } = input;
  const at = (input.now ?? new Date()).toISOString();

  const dev = await db.from("devices").select("id").eq("id", deviceId).eq("org_id", orgId).maybeSingle();
  dbErr(dev.error, "load device");
  if (!dev.data) throw new ApiError(404, "not_found", "Device not found");

  if (workerId) {
    const w = await db.from("workers").select("id").eq("id", workerId).eq("org_id", orgId).maybeSingle();
    dbErr(w.error, "load worker");
    if (!w.data) throw new ApiError(404, "not_found", "Worker not found");
  }

  const cur = await db.from("device_assignments").select("id, worker_id, starts_at").eq("device_id", deviceId).is("ends_at", null).maybeSingle();
  dbErr(cur.error, "load assignment");
  const open = cur.data as { id: string; worker_id: string; starts_at: string } | null;
  if ((open?.worker_id ?? null) === workerId) return { assigned_worker_id: workerId }; // nothing to change

  if (workerId) {
    const busy = await db.from("device_assignments").select("device_id").eq("worker_id", workerId).is("ends_at", null).maybeSingle();
    dbErr(busy.error, "load worker assignment");
    if (busy.data) throw new ApiError(409, "conflict", "That worker is already assigned to another hat. Unassign them there first.");
  }

  if (open) {
    if (new Date(at).getTime() <= new Date(open.starts_at).getTime()) {
      throw new ApiError(409, "conflict", "This hat was assigned a moment ago. Wait a second and try again.");
    }
    const closed = await db.from("device_assignments").update({ ends_at: at }).eq("id", open.id);
    dbErr(closed.error, "close assignment");
  }
  if (workerId) {
    const ins = await db.from("device_assignments").insert({ org_id: orgId, device_id: deviceId, worker_id: workerId, starts_at: at });
    if (ins.error) {
      if (open) await db.from("device_assignments").update({ ends_at: null }).eq("id", open.id); // put the old assignment back
      throw new ApiError(409, "conflict", `Could not assign the worker: ${ins.error.message}`);
    }
  }
  return { assigned_worker_id: workerId };
}
