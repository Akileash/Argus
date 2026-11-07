"use server";

import { revalidatePath } from "next/cache";
import { ApiError } from "@/lib/api/errors";
import { assignWorker } from "@/lib/api/assign";
import { createClient } from "@/lib/supabase/server";

export type AssignResult = { ok: true } | { ok: false; error: string };

/** Manager-only: give a hat to a worker (or take it back). RLS on device_assignments enforces manager + org. */
export async function assignDeviceWorker(deviceId: string, workerId: string | null): Promise<AssignResult> {
  const supabase = await createClient();
  const { data: auth } = await supabase.auth.getUser();
  if (!auth.user) return { ok: false, error: "Your session expired. Sign in again." };
  const { data: profile } = await supabase.from("profiles").select("role, org_id").eq("user_id", auth.user.id).maybeSingle();
  if (!profile || profile.role !== "manager") return { ok: false, error: "Only managers can assign workers." };
  try {
    await assignWorker(supabase, { deviceId, workerId, orgId: profile.org_id });
  } catch (e) {
    if (e instanceof ApiError) return { ok: false, error: e.message };
    return { ok: false, error: "Could not save the assignment." };
  }
  revalidatePath("/devices");
  return { ok: true };
}
