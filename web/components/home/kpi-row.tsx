"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { formatRelative, type DeviceSeen, type Kpis } from "@/lib/queries/home";

export function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}

function Tile({
  label,
  value,
  hint,
  tone,
  className,
}: {
  label: string;
  value: number;
  hint: string;
  tone: "near" | "incident" | "draft";
  className?: string;
}) {
  const bar = tone === "near" ? "bg-near-miss-line" : tone === "incident" ? "bg-incident-line" : "bg-status-draft-fg";
  return (
    <div className={cn("relative flex flex-col justify-between rounded-md border border-line-strong bg-surface p-3 pl-4", className)}>
      <span className={cn("absolute inset-y-0 left-0 w-1.5 rounded-l-md", bar)} aria-hidden />
      <div className="field-label">{label}</div>
      <div className="num mt-1 text-4xl font-bold leading-none" data-testid={`kpi-${tone}`}>
        {value}
      </div>
      <div className="mt-1.5 text-xs text-muted">{hint}</div>
    </div>
  );
}

export function KpiRow({ kpis, devices }: { kpis: Kpis; devices: DeviceSeen[] }) {
  const now = useNow();
  const seen = devices.filter((d) => d.lastEventAt).length;
  return (
    <section aria-label="Key figures" className="grid grid-cols-2 gap-3 lg:grid-cols-[repeat(3,minmax(0,11rem))_minmax(0,1fr)]" data-testid="kpi-row">
      <Tile label="Near misses" value={kpis.nearMissesWeek} hint="last 7 days" tone="near" />
      <Tile label="Incidents" value={kpis.incidentsWeek} hint="last 7 days" tone="incident" />
      <Tile label="Awaiting sign-off" value={kpis.awaitingSignoff} hint="draft reports" tone="draft" className="col-span-2 lg:col-span-1" />
      <div className="col-span-2 rounded-md border border-line-strong bg-surface p-3 lg:col-span-1">
        <div className="flex items-baseline justify-between gap-2">
          <div className="field-label">Devices &middot; last event seen</div>
          <div className="text-xs text-muted">
            <span className="num">{seen}</span>/<span className="num">{devices.length}</span> have reported
          </div>
        </div>
        {devices.length === 0 ? (
          <p className="mt-2 text-sm text-muted">No devices registered yet.</p>
        ) : (
          <ul className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3 xl:grid-cols-4" data-testid="device-list">
            {devices.map((d) => (
              <li key={d.deviceId} className="flex items-baseline justify-between gap-2 border-b border-line py-0.5 text-sm" title={d.workerName ?? "Unassigned"}>
                <span className="num font-semibold">{d.serial}</span>
                <span className={cn("num text-xs", d.lastEventAt ? "text-ink" : "text-muted")}>{formatRelative(d.lastEventAt, now)}</span>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-1.5 text-[0.7rem] text-muted">Hats upload only when an event fires; there is no live online or battery status.</p>
      </div>
    </section>
  );
}
