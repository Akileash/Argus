import { NextResponse } from "next/server";
import { authenticateDevice } from "@/lib/api/device-auth";
import { ApiError, handleError } from "@/lib/api/errors";
import { completeEvent } from "@/lib/api/events";
import { createAdminClient } from "@/lib/supabase/admin";
import { scheduleReportProcessing } from "@/lib/worker/schedule";

export const runtime = "nodejs";
// after() work (the report) lives as long as this limit; the response itself is sent before it starts.
export const maxDuration = 300;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function POST(request: Request, ctx: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await ctx.params;
    const db = createAdminClient();
    const device = await authenticateDevice(db, request);
    if (!UUID.test(id)) throw new ApiError(404, "not_found", "Unknown event");
    let body: { uploaded?: unknown };
    try {
      body = await request.json();
    } catch {
      throw new ApiError(400, "invalid_json", "Body is not valid JSON");
    }
    if (!Array.isArray(body.uploaded) || !body.uploaded.every((x) => typeof x === "string")) {
      throw new ApiError(422, "validation_failed", "`uploaded` must be an array of filenames");
    }
    const result = await completeEvent(db, device, id, body.uploaded as string[]);
    // Report processing runs after the response is sent (never delays or fails the device's request).
    // hat_removed / module_fallback are already `ready` at ingest, so they never reach this branch.
    if (result.status === "uploaded") scheduleReportProcessing();
    return NextResponse.json(result);
  } catch (e) {
    return handleError(e);
  }
}
