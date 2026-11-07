import { cn } from "@/lib/utils";
import { riskBand, RISK_LABELS } from "@/lib/risk";

const base = "inline-flex items-center rounded-sm border px-1.5 py-0.5 text-[0.7rem] font-semibold uppercase tracking-wide whitespace-nowrap";

export type EventClass = "near_miss" | "incident" | "ppe_log" | "system";

const CLASS_STYLE: Record<EventClass, { label: string; cls: string }> = {
  near_miss: { label: "Near miss", cls: "bg-near-miss-bg text-near-miss-fg border-near-miss-line" },
  incident: { label: "Incident", cls: "bg-incident-bg text-incident-fg border-incident-line" },
  ppe_log: { label: "PPE log", cls: "bg-ppe-bg text-ppe-fg border-ppe-line" },
  system: { label: "System", cls: "bg-system-bg text-system-fg border-system-line" },
};

export function ClassChip({ value, className }: { value: EventClass; className?: string }) {
  const s = CLASS_STYLE[value];
  return <span className={cn(base, s.cls, className)}>{s.label}</span>;
}

const RISK_CLS = [
  "",
  "bg-risk-1-bg text-risk-1-fg border-transparent",
  "bg-risk-2-bg text-risk-2-fg border-transparent",
  "bg-risk-3-bg text-risk-3-fg border-transparent",
  "bg-risk-4-bg text-risk-4-fg border-transparent",
  "bg-risk-5-bg text-risk-5-fg border-transparent",
];

/** Risk chip for a 1..25 score; null renders an em dash. */
export function RiskChip({ score, className }: { score: number | null | undefined; className?: string }) {
  if (score == null) return <span className="num text-muted">&mdash;</span>;
  const band = riskBand(score);
  return (
    <span className={cn(base, "gap-1", RISK_CLS[band], className)} title={`Risk ${score}: ${RISK_LABELS[band]}`}>
      <span className="num">{score}</span>
      <span>{RISK_LABELS[band]}</span>
    </span>
  );
}

const STATUS_CLS: Record<string, string> = {
  draft: "bg-status-draft-bg text-status-draft-fg border-transparent",
  signed: "bg-status-signed-bg text-status-signed-fg border-transparent",
  received: "bg-status-progress-bg text-status-progress-fg border-transparent",
  uploading: "bg-status-progress-bg text-status-progress-fg border-transparent",
  uploaded: "bg-status-progress-bg text-status-progress-fg border-transparent",
  analyzing: "bg-status-progress-bg text-status-progress-fg border-transparent",
  ready: "bg-status-signed-bg text-status-signed-fg border-transparent",
  failed: "bg-status-failed-bg text-status-failed-fg border-transparent",
};

/** Event status (received..failed) or report status (draft/signed). */
export function StatusChip({ status, className }: { status: string; className?: string }) {
  return <span className={cn(base, STATUS_CLS[status] ?? STATUS_CLS.received, className)}>{status}</span>;
}
