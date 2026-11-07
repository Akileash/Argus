import { NextResponse } from "next/server";
import { authenticateDevice } from "@/lib/api/device-auth";
import { ApiError, handleError } from "@/lib/api/errors";
import { ingestEvent } from "@/lib/api/events";
import { validateEvent } from "@/lib/contracts/event";
import { createAdminClient } from "@/lib/supabase/admin";

export const runtime = "nodejs";

export async function POST(request: Request) {
  try {
    const db = createAdminClient();
    const device = await authenticateDevice(db, request);
    let body: unknown;
    try {
      body = await request.json();
    } catch {
      throw new ApiError(400, "invalid_json", "Body is not valid JSON");
    }
    const v = validateEvent(body);
    if (!v.ok) {
      return NextResponse.json({ error: { code: "validation_failed", message: "Event payload failed schema validation" }, errors: v.errors }, { status: 422 });
    }
    const res = await ingestEvent(db, device, v.event);
    return NextResponse.json(res.body, { status: res.httpStatus });
  } catch (e) {
    return handleError(e);
  }
}
