import { NextResponse } from "next/server";
import { registerDevice } from "@/lib/api/register";
import { ApiError, handleError } from "@/lib/api/errors";
import { createAdminClient } from "@/lib/supabase/admin";
import { createClient } from "@/lib/supabase/server";

export const runtime = "nodejs";

export async function POST(request: Request) {
  try {
    const supabase = await createClient();
    const { data: auth } = await supabase.auth.getUser();
    if (!auth.user) throw new ApiError(401, "unauthorized", "Sign in required");
    const { data: profile } = await supabase.from("profiles").select("org_id, role").eq("user_id", auth.user.id).maybeSingle();
    if (!profile) throw new ApiError(403, "forbidden", "No profile for this user");
    if (profile.role !== "manager") throw new ApiError(403, "forbidden", "Manager role required");

    let body: { serial?: unknown; org_id?: unknown; rotate?: unknown };
    try {
      body = await request.json();
    } catch {
      throw new ApiError(400, "invalid_json", "Body is not valid JSON");
    }
    const res = await registerDevice(createAdminClient(), {
      serial: body.serial,
      orgId: (body.org_id as string | undefined) ?? profile.org_id,
      sessionOrgId: profile.org_id,
      rotate: body.rotate === true,
    });
    return NextResponse.json(res, { status: 201 });
  } catch (e) {
    return handleError(e);
  }
}
