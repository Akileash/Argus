"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

export function NavLink({ href, children }: { href: string; children: React.ReactNode }) {
  const path = usePathname();
  const active = href === "/" ? path === "/" || path.startsWith("/incidents") : path.startsWith(href);
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={cn(
        "relative flex h-12 items-center px-1 transition-colors duration-150 hover:text-accent-text",
        active ? "text-ink after:absolute after:inset-x-0 after:bottom-0 after:h-0.5 after:bg-accent" : "text-muted",
      )}
    >
      {children}
    </Link>
  );
}
