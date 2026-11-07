import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";


/**
 * Refreshes the Supabase session cookie on every request. Pages enforce sign-in themselves.
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

  await supabase.auth.getUser(); // refreshes the session cookie when needed

  // No redirects here. Next rewrites the host of absolute redirects (127.0.0.1 -> localhost), which loses the
  // session cookie. Pages redirect themselves (lib/session.ts, login page), which stays on the visitor's address.
  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)"],
};
