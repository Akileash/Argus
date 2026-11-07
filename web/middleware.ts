import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

/**
 * Redirect on the address the visitor actually used. nextUrl carries the server's own host (localhost),
 * which breaks anyone on the laptop's IP address: their session cookie belongs to that address.
 */
function redirectTo(request: NextRequest, pathname: string) {
  const url = request.nextUrl.clone();
  url.pathname = pathname;
  url.search = "";
  const host = request.headers.get("x-forwarded-host") ?? request.headers.get("host");
  if (host) url.host = host;
  return NextResponse.redirect(url);
}

/**
 * Protects every app route. Unauthenticated visitors go to /login.
 * /api/** is not redirected: device routes use bearer tokens, dashboard-API routes check the session themselves.
 */
export async function middleware(request: NextRequest) {
  let response = NextResponse.next({ request });
  const supabase = createServerClient(process.env.NEXT_PUBLIC_SUPABASE_URL!, process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!, {
    cookies: {
      getAll: () => request.cookies.getAll(),
      setAll(list) {
        for (const { name, value } of list) request.cookies.set(name, value);
        response = NextResponse.next({ request });
        for (const { name, value, options } of list) response.cookies.set(name, value, options);
      },
    },
  });

  const { data } = await supabase.auth.getUser();
  const path = request.nextUrl.pathname;

  if (path.startsWith("/api/")) return response;
  if (!data.user && path !== "/login") {
    return redirectTo(request, "/login");
  }
  if (data.user && path === "/login") {
    return redirectTo(request, "/");
  }
  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)"],
};
