import { Card, CardContent } from "@/components/ui/card";
import { Wordmark } from "@/components/shell/wordmark";
import { isDev } from "@/lib/env";
import { LoginForm } from "./login-form";

export default function LoginPage() {
  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="anim-reveal w-full max-w-sm">
        <div className="mb-6">
          <Wordmark size="lg" />
          <p className="mt-2 text-sm text-muted">Incident and near-miss reporting from the hard hat.</p>
        </div>
        <Card className="shadow-pop">
          <CardContent className="p-6">
            <h1 className="mb-5 text-lg font-bold tracking-tight">Sign in</h1>
            <LoginForm demoHint={isDev} />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
