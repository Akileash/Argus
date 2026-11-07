import { NextResponse } from "next/server";
import { authenticateDevice } from "@/lib/api/device-auth";
import { ApiError, handleError } from "@/lib/api/errors";
import { completeEvent } from "@/lib/api/events";
import { createAdminClient } from "@/lib/supabase/admin";

export const runtime = "nodejs";

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
    return NextResponse.json(await completeEvent(db, device, id, body.uploaded as string[]));
  } catch (e) {
    return handleError(e);
  }
}
