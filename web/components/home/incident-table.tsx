"use client";

import { useRouter } from "next/navigation";
import { motion } from "motion/react";
import { Sparkles } from "lucide-react";
import { ClassChip, RiskChip, StatusChip } from "@/components/chips";
import { cn } from "@/lib/utils";
import { formatEventTime, type IncidentRow } from "@/lib/queries/home";

const PROCESSING = new Set(["received", "uploading", "uploaded", "analyzing"]);

function StatusCell({ row }: { row: IncidentRow }) {
  return (
    <div className="flex flex-wrap items-center gap-1">
      {PROCESSING.has(row.status) || row.status === "failed" ? (
        <StatusChip status={row.status} className={PROCESSING.has(row.status) ? "animate-pulse motion-reduce:animate-none" : ""} />
      ) : row.reportStatus ? (
        <StatusChip status={row.reportStatus} />
      ) : (
        <StatusChip status={row.status} />
      )}
      {row.aiSuggestsReclass && row.llmSuggestedClass && (
        <span
          className="inline-flex items-center gap-1 rounded-sm border border-accent-text/40 bg-accent-soft px-1 py-0.5 text-[0.65rem] font-semibold uppercase tracking-wide text-accent-text"
          title={`AI suggests reclassifying as ${row.llmSuggestedClass.replace("_", " ")}`}
        >
          <Sparkles className="h-3 w-3" aria-hidden /> AI reclass
        </span>
      )}
    </div>
  );
}

export function IncidentTable({ rows, freshIds }: { rows: IncidentRow[]; freshIds: ReadonlySet<string> }) {
  const router = useRouter();
  const open = (id: string) => router.push(`/incidents/${id}`);
  return (
    <div className="overflow-x-auto rounded-md border border-line-strong bg-surface">
      <table className="w-full min-w-[56rem] border-collapse text-sm" data-testid="incident-table">
        <thead>
          <tr className="bg-surface-2 text-left">
            {["No.", "Time", "Type", "Class", "Risk", "Worker", "Crew", "Device", "Status"].map((h) => (
              <th key={h} scope="col" className="field-label whitespace-nowrap border-b border-line-strong px-3 py-2 font-semibold">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <motion.tr
              key={r.id}
              data-testid="incident-row"
              data-event-id={r.id}
              data-class={r.cls}
              initial={freshIds.has(r.id) ? { opacity: 0, y: -10, backgroundColor: "#ffe9d9" } : false}
              animate={freshIds.has(r.id) ? { opacity: 1, y: 0, backgroundColor: "#ffe9d900" } : undefined}
              transition={{ duration: 0.25, ease: "easeOut", backgroundColor: { duration: 2.4, ease: "easeOut" } }}
                            ref={(el: HTMLTableRowElement | null) => {
                if (el && freshIds.has(r.id)) setTimeout(() => el.style.removeProperty("background-color"), 2800);
              }}
              tabIndex={0}
              role="link"
              aria-label={`${r.typeLabel} ${r.ref}`}
              onClick={() => open(r.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  open(r.id);
                }
              }}
              className={cn(
                "h-9 cursor-pointer border-b border-line align-middle hover:bg-surface-2 focus-visible:bg-surface-2",
                r.quiet && "text-muted",
              )}
            >
              <td className="num whitespace-nowrap px-3 py-1.5 font-semibold">{r.ref}</td>
              <td className="num whitespace-nowrap px-3 py-1.5">{formatEventTime(r.t0)}</td>
              <td className={cn("whitespace-nowrap px-3 py-1.5", !r.quiet && "font-medium")}>{r.typeLabel}</td>
              <td className="px-3 py-1.5">
                <ClassChip value={r.cls} className={r.quiet ? "opacity-80" : undefined} />
              </td>
              <td className="px-3 py-1.5">{r.quiet ? <span className="text-muted">&mdash;</span> : <RiskChip score={r.severity} />}</td>
              <td className="whitespace-nowrap px-3 py-1.5">{r.worker ?? <span className="text-muted">Unassigned</span>}</td>
              <td className="whitespace-nowrap px-3 py-1.5">{r.crew ?? <span className="text-muted">&mdash;</span>}</td>
              <td className="num whitespace-nowrap px-3 py-1.5">{r.device ?? "—"}</td>
              <td className="px-3 py-1.5">
                <StatusCell row={r} />
              </td>
            </motion.tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
