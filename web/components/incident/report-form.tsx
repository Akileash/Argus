"use client";

import { createContext, useContext, useMemo, useState } from "react";
import { Plus, Undo2, X } from "lucide-react";
import { LOCATION_PLACEHOLDER, REPORT_FOOTER } from "@argus/shared";
import { ClassChip, RiskChip, type EventClass } from "@/components/chips";
import { cn } from "@/lib/utils";
import {
  CAUSE_KIND_OPTIONS,
  CONTROL_OPTIONS,
  SCALAR_FIELDS,
  SOURCE_OPTIONS,
  applyFieldValue,
  diffPaths,
  fieldValue,
  getAt,
  normalizeReport,
  sectionEdited,
  setAt,
  type FieldSpec,
  type ReportJson,
} from "@/lib/report/model";
import { Time } from "./time";
import { RiskMatrix } from "./risk-matrix";

interface Ctx {
  report: ReportJson;
  draft: ReportJson;
  edited: Set<string>;
  readOnly: boolean;
  update: (next: ReportJson) => void;
}
const ReportCtx = createContext<Ctx | null>(null);
const useRep = () => {
  const c = useContext(ReportCtx);
  if (!c) throw new Error("ReportCtx missing");
  return c;
};

const inputCls =
  "w-full rounded-sm border border-line-strong bg-surface px-2 py-1.5 text-sm leading-snug [field-sizing:content] focus-visible:outline-2 focus-visible:outline-ring disabled:border-transparent disabled:bg-transparent disabled:px-0 disabled:opacity-100";

/* --------------------------------- shells --------------------------------- */

/** Wraps one editable value: AI/edited marker, view-original, revert. */
function Marked({ path, label, children, compact }: { path: string; label?: string; children: React.ReactNode; compact?: boolean }) {
  const { draft, edited, readOnly, update, report } = useRep();
  const [showOrig, setShowOrig] = useState(false);
  const isEdited = edited.has(path);
  const original = getAt(draft, path);
  const originalText = original == null || original === "" ? "(empty)" : String(original);
  return (
    <div
      className={cn(
        "border-l-2 pl-2",
        isEdited ? "border-ink" : readOnly ? "border-transparent" : "border-accent bg-accent-soft/30",
      )}
      data-edited={isEdited || undefined}
      data-path={path}
    >
      {(label || isEdited || !readOnly) && !compact && (
        <div className="mb-0.5 flex items-center gap-2">
          {label && <span className="field-label">{label}</span>}
          {isEdited ? (
            <>
              <span className="whitespace-nowrap rounded-sm bg-ink px-1 py-px text-[0.6rem] font-semibold uppercase tracking-wide text-surface">edited by manager</span>
              <button type="button" className="text-xs text-accent-text underline" onClick={() => setShowOrig((s) => !s)} aria-expanded={showOrig}>
                {showOrig ? "Hide original" : "View original"}
              </button>
              {!readOnly && (
                <button
                  type="button"
                  className="inline-flex items-center gap-0.5 text-xs text-muted hover:text-ink"
                  onClick={() => update(normalizeReport(setAt(report, path, original)))}
                >
                  <Undo2 className="h-3 w-3" /> Revert
                </button>
              )}
            </>
          ) : (
            !readOnly && <span className="text-[0.6rem] font-semibold uppercase tracking-wide text-accent-text">AI draft</span>
          )}
        </div>
      )}
      {children}
      {isEdited && compact && (
        <div className="mt-0.5 flex items-center gap-2">
          <span className="whitespace-nowrap rounded-sm bg-ink px-1 py-px text-[0.6rem] font-semibold uppercase tracking-wide text-surface">edited by manager</span>
          <button type="button" className="text-xs text-accent-text underline" onClick={() => setShowOrig((s) => !s)}>
            {showOrig ? "Hide original" : "View original"}
          </button>
        </div>
      )}
      {isEdited && showOrig && (
        <div className="mt-1 rounded-sm border border-dashed border-line-strong bg-surface-2 px-2 py-1 text-xs text-muted" data-testid="original-text">
          <span className="font-semibold">Original AI draft: </span>
          <span className="whitespace-pre-wrap">{originalText}</span>
        </div>
      )}
    </div>
  );
}

function Section({ n, title, children, edited }: { n: number; title: string; children: React.ReactNode; edited?: boolean }) {
  return (
    <section className="rounded-md border border-line bg-surface shadow-card" aria-labelledby={`sec-${n}`}>
      <h2 id={`sec-${n}`} className="flex items-center gap-2 border-b border-line bg-surface-2 px-3 py-1.5 text-sm font-bold">
        <span className="num text-muted">{n}</span>
        {title}
        {edited && <span className="ml-auto text-[0.65rem] font-semibold uppercase tracking-wide text-muted">edited</span>}
      </h2>
      <div className="space-y-3 p-3">{children}</div>
    </section>
  );
}

/* --------------------------------- inputs --------------------------------- */

function Field({ spec, label = spec.label, compact, className }: { spec: FieldSpec; label?: string; compact?: boolean; className?: string }) {
  const { report, readOnly, update } = useRep();
  const value = fieldValue(report, spec.path);
  const on = (raw: string) => update(applyFieldValue(report, spec, raw));
  let control: React.ReactNode;
  if (spec.kind === "select") {
    control = (
      <select className={cn(inputCls, "pr-6", className)} value={value} disabled={readOnly} aria-label={label} onChange={(e) => on(e.target.value)}>
        {spec.options!.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    );
  } else if (spec.kind === "textarea") {
    control = <textarea rows={2} className={cn(inputCls, "resize-y", className)} value={value} readOnly={readOnly} disabled={readOnly} aria-label={label} onChange={(e) => on(e.target.value)} />;
  } else {
    control = <input type="text" className={cn(inputCls, className)} value={value} disabled={readOnly} aria-label={label} onChange={(e) => on(e.target.value)} />;
  }
  return <Marked path={spec.path} label={compact ? undefined : label} compact={compact}>{control}</Marked>;
}

const mk = (path: string, kind: FieldSpec["kind"], label: string, options?: FieldSpec["options"]): FieldSpec => ({ path, kind, label, options });

function ListEditor<T>({
  path,
  items,
  blank,
  addLabel,
  children,
}: {
  path: string;
  items: T[];
  blank: T;
  addLabel: string;
  children: (item: T, i: number) => React.ReactNode;
}) {
  const { report, readOnly, update } = useRep();
  return (
    <div className="space-y-2">
      {items.length === 0 && <p className="text-sm text-muted">None recorded.</p>}
      {items.map((item, i) => (
        <div key={i} className="flex items-start gap-2 rounded-sm border border-line p-2" data-testid={`${path}-item`}>
          <div className="min-w-0 flex-1">{children(item, i)}</div>
          {!readOnly && (
            <button
              type="button"
              className="rounded-sm p-1 text-muted hover:bg-surface-2 hover:text-ink"
              aria-label={`Remove item ${i + 1}`}
              onClick={() => update(setAt(report, path, items.filter((_, j) => j !== i)))}
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>
      ))}
      {!readOnly && (
        <button
          type="button"
          className="inline-flex items-center gap-1 text-sm font-medium text-accent-text hover:underline"
          onClick={() => update(setAt(report, path, [...items, blank]))}
        >
          <Plus className="h-4 w-4" /> {addLabel}
        </button>
      )}
    </div>
  );
}

function Num({ path, label, className }: { path: string; label: string; className?: string }) {
  const { report, readOnly, update } = useRep();
  return (
    <Marked path={path} label={label}>
      <input
        type="number"
        step="0.1"
        className={cn(inputCls, "num", className)}
        value={String(getAt(report, path) ?? "")}
        disabled={readOnly}
        aria-label={label}
        onChange={(e) => update(setAt(report, path, e.target.value === "" ? 0 : Number(e.target.value)))}
      />
    </Marked>
  );
}

/* ---------------------------------- form ---------------------------------- */

const S = SCALAR_FIELDS;

export function ReportForm({
  report,
  draft,
  readOnly,
  onChange,
  displayClass,
}: {
  report: ReportJson;
  draft: ReportJson;
  readOnly: boolean;
  onChange: (r: ReportJson) => void;
  displayClass: EventClass;
}) {
  const edited = useMemo(() => diffPaths(draft, report), [draft, report]);
  const ctx: Ctx = { report, draft, edited, readOnly, update: onChange };
  const h = report.header;
  const e = (p: string) => sectionEdited(edited, p);

  return (
    <ReportCtx.Provider value={ctx}>
      <div className="space-y-3" data-testid="report-form">
        <Section n={1} title="Header" edited={e("header")}>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
            <Info label="Report no."><span className="num font-semibold">{h.report_no}</span></Info>
            <Info label="Classification"><ClassChip value={displayClass} /></Info>
            <Info label="Occurred"><span className="num"><Time iso={h.occurred_at} /></span></Info>
            <Info label="Site">{h.site}</Info>
            <Info label="Worker">{h.worker}</Info>
            <Info label="Crew">{h.crew}</Info>
            <Info label="Supervisor">{h.supervisor}</Info>
            <Info label="Device"><span className="num">{h.device}</span></Info>
          </dl>
          <Field spec={S.location} />
          {!readOnly && h.location_on_site === LOCATION_PLACEHOLDER && (
            <p className="text-xs text-muted">No GPS. Fill this in if you know where on site it happened.</p>
          )}
        </Section>

        <Section n={2} title="Summary" edited={e("summary")}>
          <Field spec={S.summary} compact />
        </Section>

        <Section n={3} title="Sequence of events" edited={e("sequence_of_events")}>
          <ListEditor path="sequence_of_events" items={report.sequence_of_events} blank={{ offset_s: 0, description: "", source: "inferred" as const }} addLabel="Add event">
            {(_, i) => (
              <div className="grid grid-cols-[7.5rem_8rem_1fr] gap-2">
                <Num path={`sequence_of_events.${i}.offset_s`} label="Offset (s)" />
                <Field spec={mk(`sequence_of_events.${i}.source`, "select", "Source", SOURCE_OPTIONS)} />
                <Field spec={mk(`sequence_of_events.${i}.description`, "textarea", "Description")} />
              </div>
            )}
          </ListEditor>
        </Section>

        <Section n={4} title="Evidence" edited={e("evidence")}>
          <div>
            <div className="field-label mb-1">Clip references</div>
            <ListEditor path="evidence.clip_refs" items={report.evidence.clip_refs} blank={{ offset_s: 0, observation: "" }} addLabel="Add clip reference">
              {(_, i) => (
                <div className="grid grid-cols-[7.5rem_1fr] gap-2">
                  <Num path={`evidence.clip_refs.${i}.offset_s`} label="Offset (s)" />
                  <Field spec={mk(`evidence.clip_refs.${i}.observation`, "textarea", "Observation")} />
                </div>
              )}
            </ListEditor>
          </div>
          <div>
            <div className="field-label mb-1">Key sensor values</div>
            <ListEditor path="evidence.key_sensor_values" items={report.evidence.key_sensor_values} blank={{ name: "", value: "" }} addLabel="Add value">
              {(_, i) => (
                <div className="grid grid-cols-2 gap-2">
                  <Field spec={mk(`evidence.key_sensor_values.${i}.name`, "text", "Name")} />
                  <Field spec={mk(`evidence.key_sensor_values.${i}.value`, "text", "Value")} />
                </div>
              )}
            </ListEditor>
          </div>
          <Field spec={S.environment} />
        </Section>

        <Section n={5} title="Hazard identification" edited={e("hazard")}>
          <div className="grid grid-cols-2 gap-3">
            <Field spec={S.hazardCategory} />
            <Field spec={S.energy} />
          </div>
        </Section>

        <Section n={6} title="Risk assessment" edited={e("risk")}>
          <div className="grid grid-cols-[1fr_1fr_auto] items-end gap-3">
            <Field spec={S.severity} />
            <Field spec={S.likelihood} />
            <div>
              <div className="field-label mb-0.5">Score</div>
              <div className="flex items-center gap-2" data-testid="risk-score" data-score={report.risk.score}>
                <span className="num text-xl font-bold">{report.risk.score}</span>
                <RiskChip score={report.risk.score} />
              </div>
            </div>
          </div>
          <div className="flex flex-wrap gap-4">
            <div className="min-w-0 flex-1 space-y-3">
              <Field spec={S.severityWhy} />
              <Field spec={S.likelihoodWhy} />
            </div>
            <RiskMatrix severity={report.risk.severity} likelihood={report.risk.likelihood} />
          </div>
        </Section>

        <Section n={7} title="Causes" edited={e("causes")}>
          <div>
            <div className="field-label mb-1">Immediate causes</div>
            <ListEditor path="causes.immediate" items={report.causes.immediate} blank={{ kind: "substandard_act" as const, description: "" }} addLabel="Add cause">
              {(_, i) => (
                <div className="grid grid-cols-[10rem_1fr] gap-2">
                  <Field spec={mk(`causes.immediate.${i}.kind`, "select", "Kind", CAUSE_KIND_OPTIONS)} />
                  <Field spec={mk(`causes.immediate.${i}.description`, "textarea", "Description")} />
                </div>
              )}
            </ListEditor>
          </div>
          <div>
            <div className="field-label mb-1">Root cause: 5 whys</div>
            <ListEditor path="causes.root_five_whys" items={report.causes.root_five_whys} blank="" addLabel="Add why">
              {(_, i) => (
                <div className="flex gap-2">
                  <span className="num pt-1.5 text-sm font-semibold text-muted">{i + 1}.</span>
                  <div className="flex-1">
                    <Field spec={mk(`causes.root_five_whys.${i}`, "textarea", `Why ${i + 1}`)} compact />
                  </div>
                </div>
              )}
            </ListEditor>
          </div>
        </Section>

        <Section n={8} title="Corrective and preventive actions" edited={e("corrective_actions")}>
          <p className="text-xs text-muted">A plain list for the manager to act on elsewhere. Argus does not track owners, due dates or status.</p>
          <ListEditor
            path="corrective_actions"
            items={report.corrective_actions}
            blank={{ description: "", control_level: "administrative" as const, suggested_owner_role: "" }}
            addLabel="Add action"
          >
            {(_, i) => (
              <div className="space-y-2">
                <Field spec={mk(`corrective_actions.${i}.description`, "textarea", "Action")} />
                <div className="grid grid-cols-2 gap-2">
                  <Field spec={mk(`corrective_actions.${i}.control_level`, "select", "Control level", CONTROL_OPTIONS)} />
                  <Field spec={mk(`corrective_actions.${i}.suggested_owner_role`, "text", "Suggested owner role")} />
                </div>
              </div>
            )}
          </ListEditor>
        </Section>

        <Section n={9} title="Regulatory note" edited={e("regulatory_note")}>
          <Field spec={S.regulatory} compact />
        </Section>

        <Section n={10} title="Confidence and gaps" edited={e("confidence_and_gaps")}>
          <Field spec={S.gaps} compact />
        </Section>

        <SignOffBlock report={report} />
        <p className="px-1 text-xs text-muted" data-testid="report-footer">{REPORT_FOOTER}</p>
      </div>
    </ReportCtx.Provider>
  );
}

function Info({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="field-label">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

export function SignOffBlock({ report }: { report: ReportJson }) {
  const s = report.sign_off;
  const signed = !!s.signed_at;
  return (
    <section className="rounded-md border border-line bg-surface shadow-card" aria-labelledby="sec-11">
      <h2 id="sec-11" className="flex items-center gap-2 border-b border-line bg-surface-2 px-3 py-1.5 text-sm font-bold">
        <span className="num text-muted">11</span> Sign-off
      </h2>
      <div className="p-3 text-sm" data-testid="signoff-block">
        {signed ? (
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2">
            <Info label="Signed by">{s.signed_by}</Info>
            <Info label="Signed at"><span className="num"><Time iso={s.signed_at} /></span></Info>
            <Info label="Final classification">{s.final_class ? <ClassChip value={s.final_class} /> : "—"}</Info>
            <Info label="AI reclassification">
              <span className="capitalize">{(s.reclass_decision ?? "not applicable").replace("_", " ")}</span>
            </Info>
          </dl>
        ) : (
          <p className="text-muted">Not signed. A manager signs off after review; the report then becomes read-only.</p>
        )}
      </div>
    </section>
  );
}
