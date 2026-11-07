"use client";

import { useEffect } from "react";
import { Printer } from "lucide-react";
import { Button } from "@/components/ui/button";

/** Opens the browser print dialog once the page has settled; the button is the fallback (and hidden in the printout). */
export function AutoPrint() {
  useEffect(() => {
    if (new URLSearchParams(window.location.search).has("noprint")) return;
    const t = setTimeout(() => window.print(), 600);
    return () => clearTimeout(t);
  }, []);
  return (
    <div className="no-print mx-auto flex max-w-[190mm] items-center justify-between gap-3 px-6 pt-4">
      <p className="text-sm text-muted">Choose &ldquo;Save as PDF&rdquo; in the print dialog. A4 or Letter both work.</p>
      <Button type="button" onClick={() => window.print()} data-testid="print-button">
        <Printer className="h-4 w-4" /> Print / Save as PDF
      </Button>
    </div>
  );
}
