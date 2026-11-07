"use client";

import { useCallback, useEffect, useMemo, useRef, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { FileDown, PenLine, Save, Sparkles, Check, X } from "lucide-react";
import { ClassChip, RiskChip, StatusChip, type EventClass } from "@/components/chips";
import { Button } from "@/components/ui/button";
import { createClient } from "@/lib/supabase/client";
import { Time } from "./time";
import type { IncidentData } from "@/lib/queries/incident";
import { saveReport, type ReclassDecision } from "@/app/(app)/incidents/[id]/actions";
import { normalizeReport, type ReportJson } from "@/lib/report/model";
import { EvidencePanel } from "./evidence-panel";
import { ReportForm } from "./report-form";
import { ProcessingStepper } from "./processing-stepper";
import { SignOffDialog } from "./sign-off-dialog";

const PROCESSING = ["received", "uploading", "uploaded", "analyzing"];
const TYPE_LABEL: Record<string, string> = {
  struck_by_near_miss: "Struck-by near miss",
  struck_by_contact: "Struck-by contact",
  head_impact: "Head impact",
  fall: "Fall",
  hat_knocked_off: "Hat knocked off",
  hat_removed: "Hat removed",
  module_fallback: "Module fallback",
};

export function IncidentView({ data, role, fullName }: { data: IncidentData; role: "manager" | "viewer"; fullName: string }) {
  const router = useRouter();
  const { report: row, trace, traceState, clipUrl, clipDurationS } = data;

  // live event fields (Realtime) layered over the server snapshot
  const [live, setLive] = useState({ status: data.event.status, error: data.event.error });
  const [videoAnalyzed, setVideoAnalyzed] = useState(false);
  useEffect(() => setLive({ status: data.event.status, error: data.event.error }), [data.event.status, data.event.error]);

  const processing = PROCESSING.includes(live.status);
  const event = { ...data.event, status: live.status, error: live.error };

  /* ---------------------------- report edit state ---------------------------- */
  const [report, setReport] = useState<ReportJson | null>(row?.report_json ?? null);
  const baseline = useRef<string>(row ? JSON.stringify(row.report_json) : "");
  const dirty = !!report && JSON.stringify(report) !== baseline.current;
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;

  useEffect(() => {
    if (row && !dirtyRef.current) {
      setReport(row.report_json);
      baseline.current = JSON.stringify(row.report_json);
    }
  }, [row]);

  const signed = row?.status === "signed";
  const canEdit = role === "manager" && row?.status === "draft";

  const [saveMsg, setSaveMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [saving, startSave] = useTransition();
  const doSave = useCallback(() => {
    if (!row || !report) return;
    startSave(async () => {
      const res = await saveReport(row.id, report);
      if (res.ok) {
        baseline.current = JSON.stringify(res.report);
        setReport(res.report);
        setSaveMsg({ kind: "ok", text: "Saved" });
        router.refresh();
      } else {
        setSaveMsg({ kind: "err", text: res.error });
        if (res.locked) router.refresh();
      }
    });
  }, [row, report, router]);

  /* ------------------------------ AI reclass state ------------------------------ */
  const suggested = event.llm_suggested_class && event.llm_suggested_class !== event.device_class ? event.llm_suggested_class : null;
  const [pendingDecision, setPendingDecision] = useState<ReclassDecision | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);

  /* -------------------------------- realtime -------------------------------- */
  useEffect(() => {
    const sb = createClient();
    const ch = sb
      .channel(`incident-${event.id}`)
      .on("postgres_changes", { event: "UPDATE", schema: "public", table: "events", filter: `id=eq.${event.id}` }, (p) => {
        const n = p.new as { status: string; error: string | null };
        setLive((cur) => {
          if (cur.status !== n.status) router.refresh();
          return { status: n.status, error: n.error };
        });
      })
      .on("postgres_changes", { event: "*", schema: "public", table: "reports", filter: `event_id=eq.${event.id}` }, () => router.refresh())
      .subscribe();
    return () => {
      void sb.removeChannel(ch);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [event.id]);

  // analyses is not in the Realtime publication: poll it while the event is processing to split step 2 / 3
  useEffect(() => {
    if (!processing && live.status !== "failed") return;
    const sb = createClient();
    let stop = false;
    const poll = async () => {
      const { data: rows } = await sb.from("analyses").select("stage").eq("event_id", event.id);
      if (!stop) setVideoAnalyzed(!!rows?.some((r) => r.stage === "video_analysis"));
    };
    void poll();
    const t = setInterval(poll, 3000);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, [processing, live.status, event.id]);

  // warn on leaving with unsaved edits
  useEffect(() => {
    if (!dirty) return;
    const h = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [dirty]);

  const displayClass: EventClass = event.final_class ?? event.device_class;
  const hdr = report?.header;
  const score = report?.risk.score ?? event.severity;
  const draft = row?.llm_draft_json ?? null;
  const showReport = !!row && !!report && !!draft && !(processing && !row);
  const failed = live.status === "failed";

  const decisionRecorded = row?.report_json.sign_off.reclass_decision;

  const printHref = useMemo(() => `/print/${event.id}`, [event.id]);

  return (
    <div className="space-y-4" data-testid="incident-detail">
      {/* header bar */}
      <header className="rounded-md border border-line bg-surface shadow-card px-4 py-3" data-testid="incident-header">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <h1 className="text-xl font-bold tracking-tight">
            <span className="num" data-testid="report-no">{row?.report_no ?? "No report yet"}</span>
          </h1>
          <ClassChip value={displayClass} />
          <RiskChip score={score} />
          {row ? <StatusChip status={row.status} /> : <StatusChip status={live.status} />}
          <span className="text-sm text-muted">{TYPE_LABEL[event.type] ?? event.type}</span>
          <div className="no-print ml-auto flex items-center gap-2">
            {dirty && canEdit && <span className="text-xs font-semibold text-near-miss-fg">Unsaved changes</span>}
            {saveMsg && (
              <span role="status" className={saveMsg.kind === "ok" ? "text-xs text-status-signed-fg" : "max-w-xs text-xs text-incident-fg"} data-testid="save-msg">
                {saveMsg.text}
              </span>
            )}
            {canEdit && (
              <Button type="button" variant="outline" size="sm" onClick={doSave} disabled={!dirty || saving} data-testid="save-report">
                <Save className="h-4 w-4" /> {saving ? "Saving…" : "Save"}
              </Button>
            )}
            {row && (
              <Button asChild variant="outline" size="sm">
                <a href={printHref} target="_blank" rel="noreferrer" data-testid="export-pdf">
                  <FileDown className="h-4 w-4" /> Export PDF
                </a>
              </Button>
            )}
            {role === "manager" && row?.status === "draft" && !processing && (
              <Button type="button" size="sm" onClick={() => setDialogOpen(true)} data-testid="signoff-open">
                <PenLine className="h-4 w-4" /> Sign off
              </Button>
            )}
          </div>
        </div>
        <dl className="mt-2 flex flex-wrap gap-x-8 gap-y-1 text-sm">
          <Meta label="Worker" value={hdr?.worker ?? data.workerName} />
          <Meta label="Crew" value={hdr?.crew ?? data.crewName} />
          <Meta label="Site" value={hdr?.site ?? data.siteName} />
          <Meta label="Occurred" value={<Time iso={event.t0} />} mono />
          {data.deviceSerial && <Meta label="Device" value={data.deviceSerial} mono />}
        </dl>
      </header>

      {/* reclass banner */}
      {suggested && row && (
        <ReclassBanner
          suggested={suggested}
          reason={event.llm_reclass_reason ?? report?.classification_review.reason ?? null}
          signed={signed}
          recorded={decisionRecorded ?? null}
          canDecide={canEdit}
          pending={pendingDecision}
          onChoose={setPendingDecision}
        />
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)]">
        <div className="lg:sticky lg:top-16 lg:self-start">
          <EvidencePanel event={event} clipUrl={clipUrl} clipDurationS={clipDurationS} trace={trace} traceState={traceState} processing={processing} />
        </div>
        <div>
          {processing || failed ? (
            <ProcessingStepper status={live.status} videoAnalyzed={videoAnalyzed} error={live.error} />
          ) : showReport && report && draft ? (
            <div className="anim-reveal" data-testid="report-reveal">
              {signed && (
                <div className="anim-banner mb-3 rounded-md border border-status-signed-fg/30 bg-status-signed-bg px-3 py-2 text-sm text-status-signed-fg" data-testid="signed-banner">
                  Signed by <strong>{row!.signed_off_by}</strong> on <Time iso={row!.signed_off_at} />. This report is locked.
                </div>
              )}
              <ReportForm
                report={report}
                draft={draft}
                readOnly={!canEdit}
                onChange={(r) => {
                  setSaveMsg(null);
                  setReport(normalizeReport(r));
                }}
                displayClass={displayClass}
              />
            </div>
          ) : (
            <div className="rounded-md border border-dashed border-line-strong bg-surface p-6 text-sm text-muted" data-testid="no-report">
              {event.device_class === "ppe_log"
                ? "PPE log entries record that the hat was removed. They have no clip and no report."
                : event.device_class === "system"
                  ? event.fallbacks_active.length > 0
                  ? `Module fallback: ${event.fallbacks_active.join(", ")} switched to its backup implementation. System events have no clip and no report.`
                  : "System events do not produce a report."
                  : "No report has been generated for this event."}
            </div>
          )}
        </div>
      </div>

      {row && report && role === "manager" && !signed && (
        <SignOffDialog
          open={dialogOpen}
          onClose={() => setDialogOpen(false)}
          onDone={() => {
            setDialogOpen(false);
            baseline.current = JSON.stringify(report);
            router.refresh();
          }}
          reportId={row.id}
          report={report}
          signerName={fullName}
          deviceClass={event.device_class}
          suggestedClass={suggested}
          suggestionReason={event.llm_reclass_reason ?? report.classification_review.reason}
          initialDecision={pendingDecision}
          hasUnsavedEdits={dirty}
        />
      )}
    </div>
  );
}

function Meta({ label, value, mono }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-baseline gap-1.5">
      <dt className="field-label">{label}</dt>
      <dd className={mono ? "num" : "font-medium"}>{value || "—"}</dd>
    </div>
  );
}

function ReclassBanner({
  suggested,
  reason,
  signed,
  recorded,
  canDecide,
  pending,
  onChoose,
}: {
  suggested: EventClass;
  reason: string | null;
  signed: boolean;
  recorded: "accepted" | "rejected" | "not_applicable" | null;
  canDecide: boolean;
  pending: ReclassDecision | null;
  onChoose: (d: ReclassDecision | null) => void;
}) {
  const label = suggested.replace("_", " ");
  return (
    <div className="anim-banner flex flex-wrap items-center gap-3 rounded-md border border-accent bg-accent-soft px-4 py-2.5 text-sm" role="status" data-testid="reclass-banner">
      <Sparkles className="h-4 w-4 shrink-0 text-accent-text" aria-hidden />
      <div className="min-w-0 flex-1">
        <span className="font-bold">AI suggests: <span className="capitalize">{label}</span></span>
        {reason && <span> &mdash; {reason}</span>}
        <div className="text-xs text-muted" data-testid="reclass-state">
          {signed
            ? `Decision recorded at sign-off: ${recorded === "accepted" ? "accepted" : recorded === "rejected" ? "rejected" : "none"}.`
            : pending
              ? `Pending: you chose to ${pending === "accepted" ? "accept" : "reject"}. Recorded when you sign off.`
              : "Pending your decision. It is recorded when you sign off."}
        </div>
      </div>
      {canDecide && !signed && (
        <div className="no-print flex gap-2">
          <Button type="button" size="sm" variant={pending === "accepted" ? "default" : "outline"} aria-pressed={pending === "accepted"} onClick={() => onChoose(pending === "accepted" ? null : "accepted")} data-testid="reclass-accept">
            <Check className="h-4 w-4" /> Accept
          </Button>
          <Button type="button" size="sm" variant={pending === "rejected" ? "default" : "outline"} aria-pressed={pending === "rejected"} onClick={() => onChoose(pending === "rejected" ? null : "rejected")} data-testid="reclass-reject">
            <X className="h-4 w-4" /> Reject
          </Button>
        </div>
      )}
    </div>
  );
}
