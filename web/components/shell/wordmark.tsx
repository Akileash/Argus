import { cn } from "@/lib/utils";

/** Argus wordmark: hard-hat glyph on a safety-orange tile + tracked caps. */
export function Wordmark({ size = "md", className }: { size?: "md" | "lg"; className?: string }) {
  const tile = size === "lg" ? "h-8 w-8" : "h-6 w-6";
  return (
    <span className={cn("inline-flex items-center gap-2.5 text-ink", className)}>
      <svg viewBox="0 0 24 24" className={cn(tile, "shrink-0")} aria-hidden>
        <rect width="24" height="24" rx="5" fill="var(--accent)" />
        <path d="M6 15.5C6 11.4 8.7 8 12 8s6 3.4 6 7.5z" fill="var(--ink)" />
        <rect x="4" y="15.5" width="16" height="2.5" rx="1.25" fill="var(--ink)" />
        <rect x="11" y="5.5" width="2" height="5" rx="1" fill="var(--ink)" />
      </svg>
      <span className={cn("font-extrabold tracking-[0.2em] text-ink", size === "lg" ? "text-2xl" : "text-[1rem]")}>ARGUS</span>
    </span>
  );
}
