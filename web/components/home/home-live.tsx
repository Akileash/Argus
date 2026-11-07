"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MotionConfig } from "motion/react";
import { AlertTriangle, Inbox, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { createClient } from "@/lib/supabase/client";
import { useOrgRealtime, type OrgChange, type RealtimeState } from "@/components/realtime/use-org-realtime";
import {
  fetchDevices,
  fetchHomeData,
  fetchKpis,
  fetchRow,
  filterRows,
  upsertRow,
  type ClassFilter,
  type DeviceSeen,
  type IncidentRow,
  type Kpis,
} from "@/lib/queries/home";
import { cn } from "@/lib/utils";
import { IncidentTable } from "./incident-table";
import { KpiRow } from "./kpi-row";

export interface HomeLiveProps {
  orgId: string;
  initialRows: IncidentRow[];
  initialKpis: Kpis;
  initialDevices: DeviceSeen[];
  /** Server-side fetch error, if any. */
  initialError?: string | null;
}

const CLASS_FILTERS: { value: ClassFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "near_miss", label: "Near miss" },
  { value: "incident", label: "Incident" },
  { value: "ppe_log", label: "PPE log" },
];

function FilterChip({ active, onClick, children, count }: { active: boolean; onClick: () => void; children: React.ReactNode; count: number }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-sm font-medium transition-colors",
        active ? "border-ink bg-ink text-surface" : "border-line-strong bg-surface text-ink hover:bg-surface-2",
      )}
    >
      {children}
      <span className={cn("num text-xs", active ? "text-surface/80" : "text-muted")}>{count}</span>
    </button>
  );
}

function LiveBadge({ state }: { state: RealtimeState }) {
  const label = state === "live" ? "Live" : state === "reconnecting" ? "Reconnecting…" : "Connecting…";
  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-medium text-muted" data-testid="live-badge" data-state={state}>
      <span
        className={cn(
          "inline-block h-2 w-2 rounded-full",
          state === "live" ? "bg-status-signed-fg" : "bg-near-miss-line animate-pulse motion-reduce:animate-none",
        )}
        aria-hidden
      />
      {label}
    </span>
  );
}

export function HomeLive({ orgId, initialRows, initialKpis, initialDevices, initialError }: HomeLiveProps) {
  const [rows, setRows] = useState(initialRows);
  const [kpis, setKpis] = useState(initialKpis);
  const [devices, setDevices] = useState(initialDevices);
  const [error, setError] = useState<string | null>(initialError ?? null);
  const [loading, setLoading] = useState(false);
  const [cls, setCls] = useState<ClassFilter>("all");
  const [needsSignoff, setNeedsSignoff] = useState(false);
  const [fresh, setFresh] = useState<ReadonlySet<string>>(new Set());
  const supabase = useRef<ReturnType<typeof createClient> | null>(null);
  const kpiTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    supabase.current = createClient();
  }, []);

  const refetchAll = useCallback(async () => {
    const c = supabase.current ?? createClient();
    setLoading(true);
    try {
      const d = await fetchHomeData(c);
      setRows(d.rows);
      setKpis(d.kpis);
      setDevices(d.devices);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load incidents");
    } finally {
      setLoading(false);
    }
  }, []);

  const refetchKpis = useCallback(() => {
    // Coalesce bursts (event update + report insert land together).
    if (kpiTimer.current) clearTimeout(kpiTimer.current);
    kpiTimer.current = setTimeout(async () => {
      const c = supabase.current;
      if (!c) return;
      try {
        const [k, d] = await Promise.all([fetchKpis(c), fetchDevices(c)]);
        setKpis(k);
        setDevices(d);
      } catch {
        // keep the last known numbers
      }
    }, 250);
  }, []);

  useEffect(() => () => void (kpiTimer.current && clearTimeout(kpiTimer.current)), []);

  const onChange = useCallback(
    async (c: OrgChange) => {
      const client = supabase.current;
      if (!client) return;
      const id: string | undefined = c.table === "events" ? c.new?.id : c.new?.event_id;
      if (!id) return;
      if (c.table === "events" && c.eventType === "DELETE") return;
      try {
        // Realtime payloads carry no joins (worker, device, report), so re-read the one row.
        const row = await fetchRow(client, id);
        if (row) {
          if (c.table === "events" && c.eventType === "INSERT") {
            setFresh((f) => new Set(f).add(id));
            setTimeout(() => setFresh((f) => (f.has(id) ? new Set([...f].filter((x) => x !== id)) : f)), 4000);
          }
          setRows((cur) => upsertRow(cur, row));
        }
      } catch {
        void refetchAll();
        return;
      }
      refetchKpis();
    },
    [refetchAll, refetchKpis],
  );

  const state = useOrgRealtime({ name: "home", orgId, onChange, onResync: refetchAll });

  // Safety net: if the socket is down, poll so the pitch screen never goes stale.
  useEffect(() => {
    if (state === "live") return;
    const t = setInterval(() => void refetchAll(), 15_000);
    return () => clearInterval(t);
  }, [state, refetchAll]);

  const counts = useMemo(
    () => ({
      all: rows.length,
      near_miss: rows.filter((r) => r.cls === "near_miss").length,
      incident: rows.filter((r) => r.cls === "incident").length,
      ppe_log: rows.filter((r) => r.cls === "ppe_log").length,
      needs: rows.filter((r) => r.needsSignoff).length,
    }),
    [rows],
  );
  const visible = useMemo(() => filterRows(rows, cls, needsSignoff), [rows, cls, needsSignoff]);

  return (
    <MotionConfig reducedMotion="user">
      <div className="flex flex-col gap-4">
        <KpiRow kpis={kpis} devices={devices} />

        <section aria-label="Incident list" className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="mr-2 text-lg font-bold tracking-tight">Incidents</h1>
            <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Filter by class">
              {CLASS_FILTERS.map((f) => (
                <FilterChip key={f.value} active={cls === f.value} onClick={() => setCls(f.value)} count={counts[f.value]}>
                  {f.label}
                </FilterChip>
              ))}
            </div>
            <span className="mx-1 hidden h-5 w-px bg-line-strong sm:inline-block" aria-hidden />
            <FilterChip active={needsSignoff} onClick={() => setNeedsSignoff((v) => !v)} count={counts.needs}>
              Needs sign-off
            </FilterChip>
            <div className="ml-auto flex items-center gap-3">
              {loading && <span className="text-xs text-muted">Refreshing&hellip;</span>}
              <LiveBadge state={state} />
            </div>
          </div>

          {error && (
            <div role="alert" className="flex items-center gap-3 rounded-md border border-status-failed-fg/40 bg-status-failed-bg px-3 py-2 text-sm text-status-failed-fg">
              <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden />
              <span className="min-w-0 flex-1">Could not load incidents: {error}</span>
              <Button size="sm" variant="outline" onClick={() => void refetchAll()}>
                <RefreshCw className="h-3.5 w-3.5" /> Retry
              </Button>
            </div>
          )}

          {visible.length > 0 ? (
            <IncidentTable rows={visible} freshIds={fresh} />
          ) : loading && rows.length === 0 ? (
            <div className="space-y-px rounded-md border border-line-strong bg-surface p-2" aria-busy="true" data-testid="incident-loading">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="h-9 animate-pulse rounded-sm bg-surface-2 motion-reduce:animate-none" />
              ))}
            </div>
          ) : (
            <div className="flex flex-col items-center gap-2 rounded-md border border-dashed border-line-strong bg-surface px-4 py-12 text-center" data-testid="incident-empty">
              <Inbox className="h-6 w-6 text-muted" aria-hidden />
              <p className="font-semibold">{rows.length === 0 ? "No events yet" : "No events match these filters"}</p>
              <p className="max-w-md text-sm text-muted">
                {rows.length === 0
                  ? "Events appear here the moment a hat uploads one. This list updates live."
                  : "Clear a filter to see the rest of the list."}
              </p>
              {rows.length > 0 && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    setCls("all");
                    setNeedsSignoff(false);
                  }}
                >
                  Clear filters
                </Button>
              )}
            </div>
          )}
        </section>
      </div>
    </MotionConfig>
  );
}
