import { Check, Loader2, TriangleAlert } from "lucide-react";
import { cn } from "@/lib/utils";

export type StepState = "done" | "active" | "pending" | "failed";

/** Maps event status (+ whether the video-analysis stage has finished) to the three visible steps. */
export function stepStates(status: string, videoAnalyzed: boolean): [StepState, StepState, StepState] {
  switch (status) {
    case "received":
    case "uploading":
      return ["active", "pending", "pending"];
    case "uploaded":
      return ["done", "active", "pending"];
    case "analyzing":
      return videoAnalyzed ? ["done", "done", "active"] : ["done", "active", "pending"];
    case "ready":
      return ["done", "done", "done"];
    case "failed":
      return videoAnalyzed ? ["done", "done", "failed"] : ["done", "failed", "pending"];
    default:
      return ["pending", "pending", "pending"];
  }
}

const LABELS = ["Clip received", "Video analyzed", "Report drafted"];
const HINTS = ["Hat is uploading the clip and sensor trace", "Gemini is watching the clip", "Claude is writing the report"];

export function ProcessingStepper({ status, videoAnalyzed, error }: { status: string; videoAnalyzed: boolean; error: string | null }) {
  const states = stepStates(status, videoAnalyzed);
  return (
    <section className="rounded-md border border-line bg-surface shadow-card p-4" aria-live="polite" data-testid="processing-stepper" data-status={status}>
      <h2 className="text-sm font-bold">{status === "failed" ? "Processing failed" : "Generating the report"}</h2>
      <p className="mb-4 text-xs text-muted">
        {status === "failed" ? "The event was received but the report could not be produced." : "This page updates on its own. The report appears here when it is ready."}
      </p>
      <ol className="space-y-3">
        {states.map((s, i) => (
          <li key={i} className="flex items-start gap-3" data-step-state={s}>
            <span
              className={cn(
                "mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-xs font-bold transition-[background-color,color,border-color] duration-200 ease-out",
                s === "done" && "border-transparent bg-status-signed-bg text-status-signed-fg",
                s === "active" && "border-transparent bg-status-progress-bg text-status-progress-fg",
                s === "failed" && "border-transparent bg-status-failed-bg text-status-failed-fg",
                s === "pending" && "border-line-strong text-muted",
              )}
            >
              {s === "done" ? <Check className="anim-banner h-4 w-4" /> : s === "active" ? <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" /> : s === "failed" ? <TriangleAlert className="h-4 w-4" /> : i + 1}
            </span>
            <div>
              <div className={cn("text-sm font-semibold", s === "pending" && "text-muted")}>{LABELS[i]}</div>
              {s === "active" && <div className="anim-banner text-xs text-muted">{HINTS[i]}</div>}
            </div>
          </li>
        ))}
      </ol>
      {status === "failed" && (
        <div className="mt-4 rounded-sm border border-incident-line bg-incident-bg p-3 text-sm text-incident-fg" data-testid="event-error">
          <div className="font-semibold">Error</div>
          <div className="whitespace-pre-wrap font-mono text-xs">{error ?? "No error detail was recorded."}</div>
        </div>
      )}
    </section>
  );
}
