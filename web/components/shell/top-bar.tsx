import Link from "next/link";
import { LogOut } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { SessionContext } from "@/lib/session";
import { NavLink } from "./nav-link";
import { Wordmark } from "./wordmark";

export function TopBar({ ctx }: { ctx: SessionContext }) {
  return (
    <header className="no-print sticky top-0 z-20 border-b border-line-strong bg-surface shadow-card">
      <div className="mx-auto flex h-12 max-w-[1600px] items-center gap-6 px-4">
        <Link href="/" aria-label="Argus home" className="flex items-center rounded-sm text-ink">
          <Wordmark />
        </Link>
        <nav aria-label="Primary" className="flex items-center gap-5 text-sm font-medium">
          <NavLink href="/">Incidents</NavLink>
          <NavLink href="/devices">Devices</NavLink>
        </nav>
        <div className="ml-auto flex items-center gap-4 text-sm">
          <div className="text-right leading-tight">
            <div className="font-semibold">{ctx.orgName}</div>
            <div className="text-xs text-muted">{ctx.siteName ?? "No site"}</div>
          </div>
          <div className="flex items-center gap-2 border-l border-line pl-4">
            <div className="text-right leading-tight">
              <div className="font-medium">{ctx.fullName}</div>
              <div className="text-xs capitalize text-muted">{ctx.role}</div>
            </div>
            <form action="/auth/signout" method="post">
              <Button type="submit" variant="ghost" size="sm" aria-label="Sign out">
                <LogOut className="h-4 w-4" /> Sign out
              </Button>
            </form>
          </div>
        </div>
      </div>
    </header>
  );
}
