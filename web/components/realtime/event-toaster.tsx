"use client";

import { useCallback, useEffect, useRef } from "react";
import { createClient } from "@/lib/supabase/client";
import { fetchRow, toastTitle, type IncidentRow } from "@/lib/queries/home";
import { ToastProvider, useToast } from "./toast";
import { useOrgRealtime, type OrgChange } from "./use-org-realtime";

/** Event types that never get an LLM report, so the toast must not promise one. */
function expectsReport(row: IncidentRow): boolean {
  return row.type !== "hat_removed" && row.type !== "module_fallback";
}

function Listener({ orgId }: { orgId: string }) {
  const toast = useToast();
  // Event ids we toasted while processing; used to flip the same toast to "ready".
  const tracked = useRef(new Map<string, IncidentRow>());
  const supabase = useRef<ReturnType<typeof createClient> | null>(null);
  useEffect(() => {
    supabase.current = createClient();
  }, []);

  const settle = useCallback(
    (row: IncidentRow) => {
      if (row.status === "ready") {
        toast.show({
          key: row.id,
          tone: "ready",
          title: "Report ready",
          body: `${toastTitle(row)}${row.reportNo ? ` (${row.reportNo})` : ""}`,
          href: `/incidents/${row.id}`,
          ttl: 12_000,
        });
        tracked.current.delete(row.id);
      } else if (row.status === "failed") {
        toast.show({ key: row.id, tone: "failed", title: "Report failed", body: toastTitle(row), href: `/incidents/${row.id}`, ttl: 20_000 });
        tracked.current.delete(row.id);
      }
    },
    [toast],
  );

  const onChange = useCallback(
    async (c: OrgChange) => {
      const client = supabase.current;
      if (!client) return;
      const id: string | undefined = c.table === "events" ? c.new?.id : c.new?.event_id;
      if (!id) return;
      try {
        if (c.table === "events" && c.eventType === "INSERT") {
          const row = await fetchRow(client, id);
          if (!row) return;
          if (!expectsReport(row)) {
            toast.show({ key: id, tone: "ready", title: row.typeLabel, body: `${[row.device, row.worker].filter(Boolean).join(" / ")} — logged`, href: `/incidents/${id}`, ttl: 8_000 });
            return;
          }
          tracked.current.set(id, row);
          toast.show({ key: id, tone: "progress", title: toastTitle(row), body: "Report generating…", href: `/incidents/${id}` });
          if (row.status === "ready" || row.status === "failed") settle(row);
        } else if (tracked.current.has(id)) {
          const row = await fetchRow(client, id);
          if (row) settle(row);
        }
      } catch {
        // Toasts are best-effort; the list refetches on its own.
      }
    },
    [toast, settle],
  );

  useOrgRealtime({ name: "toasts", orgId, onChange });
  return null;
}

/** Mount once per page (ideally in the app layout). Provides useToast() and toasts every new event for the org. */
export function EventToaster({ orgId, children }: { orgId: string; children?: React.ReactNode }) {
  return (
    <ToastProvider>
      <Listener orgId={orgId} />
      {children}
    </ToastProvider>
  );
}
