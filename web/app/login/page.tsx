import { Card, CardContent } from "@/components/ui/card";
import { isDev } from "@/lib/env";
import { LoginForm } from "./login-form";

export default function LoginPage() {
  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-5 flex items-center gap-2">
          <span className="inline-block h-5 w-5 rounded-sm bg-accent" aria-hidden />
          <span className="text-xl font-extrabold tracking-[0.18em]">ARGUS</span>
        </div>
        <Card>
          <CardContent className="p-5">
            <h1 className="mb-4 text-lg font-bold tracking-tight">Sign in</h1>
            <LoginForm demoHint={isDev} />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
