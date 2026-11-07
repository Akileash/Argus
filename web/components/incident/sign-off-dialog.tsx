"use client";

import { useEffect, useRef, useState, useTransition } from "react";
import { ClassChip, type EventClass } from "@/components/chips";
import { Button } from "@/components/ui/button";
import { signOffReport, type ReclassDecision } from "@/app/(app)/incidents/[id]/actions";
import type { ReportJson } from "@/lib/report/model";

export function SignOffDialog({
  open,
  onClose,
  onDone,
  reportId,
  eventId,
  report,
  signerName,
  deviceClass,
  suggestedClass,
  suggestionReason,
  initialDecision,
  hasUnsavedEdits,
}: {
  open: boolean;
  onClose: () => void;
  onDone: () => void;
  reportId: string;
  eventId: string;
  report: ReportJson;
  signerName: string;
  deviceClass: EventClass;
  suggestedClass: EventClass | null;
  suggestionReason: string | null;
  initialDecision: ReclassDecision | null;
  hasUnsavedEdits: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [decision, setDecision] = useState<ReclassDecision | null>(initialDecision);
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) {
      setDecision(initialDecision);
      setError(null);
      d.showModal();
    } else if (!open && d.open) d.close();
  }, [open, initialDecision]);

  const needsDecision = !!suggestedClass;
  const finalClass: EventClass = needsDecision && decision === "accepted" ? suggestedClass! : deviceClass;
  const canSign = !pending && (!needsDecision || decision != null);

  const confirm = () =>
    start(async () => {
      setError(null);
      const res = await signOffReport({ reportId, eventId, report, reclassDecision: decision });
      if (res.ok) onDone();
      else {
        setError(res.error);
        if (res.locked) onDone(); // refresh to show the signed version
      }
    });

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onCancel={onClose}
      aria-labelledby="signoff-title"
      className="m-auto w-[min(32rem,calc(100vw-2rem))] rounded-md border border-line-strong bg-surface p-0 text-ink shadow-pop backdrop:bg-ink/40 [overscroll-behavior:contain]"
      data-testid="signoff-dialog"
    >
      <div className="space-y-4 p-5">
        <h2 id="signoff-title" className="text-lg font-bold">Sign off report {report.header.report_no}</h2>
        <p className="text-sm text-muted">
          Signing records your name and the time, then locks the report. This cannot be undone.
        </p>
        <dl className="grid grid-cols-[8rem_1fr] gap-y-2 text-sm">
          <dt className="field-label self-center">Signing as</dt>
          <dd className="font-semibold" data-testid="signoff-name">{signerName}</dd>
          <dt className="field-label self-center">Device class</dt>
          <dd><ClassChip value={deviceClass} /></dd>
        </dl>

        {needsDecision && (
          <fieldset className="rounded-sm border border-line p-3">
            <legend className="field-label px-1">AI reclassification</legend>
            <p className="mb-2 text-sm">
              AI suggests <ClassChip value={suggestedClass!} />{suggestionReason ? <> &mdash; {suggestionReason}</> : null}
            </p>
            {(["accepted", "rejected"] as const).map((v) => (
              <label key={v} className="flex cursor-pointer items-center gap-2 py-1 text-sm">
                <input type="radio" name="reclass" checked={decision === v} onChange={() => setDecision(v)} className="accent-[var(--accent)]" />
                {v === "accepted" ? `Accept: final class becomes ${suggestedClass!.replace("_", " ")}` : `Reject: keep ${deviceClass.replace("_", " ")}`}
              </label>
            ))}
          </fieldset>
        )}

        <div className="flex items-center gap-2 text-sm">
          <span className="field-label">Final classification</span>
          <ClassChip value={finalClass} />
        </div>

        {hasUnsavedEdits && <p className="rounded-sm bg-near-miss-bg px-2 py-1 text-sm text-near-miss-fg">Your unsaved edits will be saved as part of signing.</p>}
        {error && <p role="alert" className="rounded-sm border border-incident-line bg-incident-bg px-2 py-1 text-sm text-incident-fg" data-testid="signoff-error">{error}</p>}

        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose} disabled={pending}>Cancel</Button>
          <Button type="button" onClick={confirm} disabled={!canSign} data-testid="signoff-confirm">
            {pending ? "Signing…" : "Sign and lock"}
          </Button>
        </div>
      </div>
    </dialog>
  );
}
