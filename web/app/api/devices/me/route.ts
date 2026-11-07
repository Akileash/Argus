import { NextResponse } from "next/server";
import { authenticateDevice } from "@/lib/api/device-auth";
import { handleError } from "@/lib/api/errors";
import { createAdminClient } from "@/lib/supabase/admin";

export const runtime = "nodejs";

export async function GET(request: Request) {
  try {
    const db = createAdminClient();
    const device = await authenticateDevice(db, request);
    const a = await db
      .from("device_assignments")
      .select("worker:workers(id, full_name)")
      .eq("device_id", device.id)
      .is("ends_at", null)
      .maybeSingle();
    const w = (a.data as { worker: { id: string; full_name: string } | null } | null)?.worker ?? null;
    return NextResponse.json({
      device_id: device.id,
      serial: device.serial,
      org_id: device.org_id,
      assigned_worker: w ? { id: w.id, full_name: w.full_name } : null,
      server_time: new Date().toISOString(),
    });
  } catch (e) {
    return handleError(e);
  }
}
