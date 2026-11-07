import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";

export interface SessionContext {
  userId: string;
  email: string;
  fullName: string;
  role: "manager" | "viewer";
  orgId: string;
  orgName: string;
  siteName: string | null;
}

/** Current user + org/site names for the app shell. Redirects to /login when signed out. RLS-scoped. */
export async function getSessionContext(): Promise<SessionContext> {
  const supabase = await createClient();
  const { data: auth } = await supabase.auth.getUser();
  if (!auth.user) redirect("/login");

  const { data: profile } = await supabase
    .from("profiles")
    .select("full_name, role, org_id")
    .eq("user_id", auth.user.id)
    .maybeSingle();
  if (!profile) redirect("/login?error=no_profile");

  const [{ data: org }, { data: site }] = await Promise.all([
    supabase.from("organizations").select("name").eq("id", profile.org_id).maybeSingle(),
    supabase.from("sites").select("name").eq("org_id", profile.org_id).order("created_at").limit(1).maybeSingle(),
  ]);

  return {
    userId: auth.user.id,
    email: auth.user.email ?? "",
    fullName: profile.full_name,
    role: profile.role,
    orgId: profile.org_id,
    orgName: org?.name ?? "Organization",
    siteName: site?.name ?? null,
  };
}
