import { NextResponse, type NextRequest } from "next/server";
import { createClient } from "@/lib/supabase/server";

export async function POST(request: NextRequest) {
  const supabase = await createClient();
  await supabase.auth.signOut();
  const url = new URL("/login", request.url);
  // Stay on the address the visitor used (request.url carries the server's own host).
  const host = request.headers.get("x-forwarded-host") ?? request.headers.get("host");
  if (host) url.host = host;
  return NextResponse.redirect(url, { status: 303 });
}
