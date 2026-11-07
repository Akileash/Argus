import { Card, CardContent } from "@/components/ui/card";
import { Wordmark } from "@/components/shell/wordmark";
import { Button } from "@/components/ui/button";
import { isDev } from "@/lib/env";
import { LoginForm } from "./login-form";
import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import { signOut } from "@/app/auth/signout/actions";

export const dynamic = "force-dynamic";

export default async function LoginPage() {
  const supabase = await createClient();
  const { data } = await supabase.auth.getUser();
  let noProfile = false;
  if (data.user) {
    const { data: profile } = await supabase.from("profiles").select("user_id").eq("user_id", data.user.id).maybeSingle();
    // Signed in with a profile: nothing to do here. Signed in WITHOUT one: do not bounce to "/" (the app sends
    // such users back here), show the problem instead.
    if (profile) redirect("/");
    noProfile = true;
  }
  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="anim-reveal w-full max-w-sm">
        <div className="mb-6">
          <Wordmark size="lg" />
          <p className="mt-2 text-sm text-muted">Incident and near-miss reporting from the hard hat.</p>
        </div>
        <Card className="shadow-pop">
          <CardContent className="p-6">
            {noProfile ? (
              <div className="space-y-4" data-testid="no-profile">
                <h1 className="text-lg font-bold tracking-tight">No Argus profile</h1>
                <p role="alert" className="text-sm font-medium text-danger">
                  Your account has no Argus profile — ask an admin.
                </p>
                <p className="text-sm text-muted">You are signed in as {data.user?.email}. Sign out to use a different account.</p>
                <form action={signOut}>
                  <Button type="submit" variant="outline" className="w-full" data-testid="no-profile-signout">
                    Sign out
                  </Button>
                </form>
              </div>
            ) : (
              <>
                <h1 className="mb-5 text-lg font-bold tracking-tight">Sign in</h1>
                <LoginForm demoHint={isDev} />
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
