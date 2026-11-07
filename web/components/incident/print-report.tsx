import { REPORT_FOOTER } from "@argus/shared";
import type { SensorTrace } from "@argus/shared";
import { ClassChip, RiskChip, StatusChip, type EventClass } from "@/components/chips";
import { Time } from "./time";
import { fmtPeakG } from "@/lib/report/format";
import { fmtOffset, windowDomain } from "@/lib/report/timeline";
import {
  CAUSE_KIND_OPTIONS,
  CONTROL_OPTIONS,
  HAZARD_OPTIONS,
  LIKELIHOOD_LABELS,
  SEVERITY_LABELS,
  type ReportJson,
} from "@/lib/report/model";
import type { IncidentEvent } from "@/lib/queries/incident";
import { RiskMatrix } from "./risk-matrix";
import { SensorChart } from "./sensor-chart";

const label = (opts: readonly { value: string | number; label: string }[], v: string) => opts.find((o) => o.value === v)?.label ?? v;

function Sec({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <section className="break-inside-avoid border-t border-line-strong pt-2">
      <h2 className="mb-1 text-[0.8rem] font-bold uppercase tracking-wide">
        <span className="num mr-1 text-muted">{n}.</span>
        {title}
      </h2>
      {children}
    </section>
  );
}

function Table({ head, rows }: { head: string[]; rows: React.ReactNode[][] }) {
  if (rows.length === 0) return <p className="text-muted">None recorded.</p>;
  return (
    <table className="w-full border-collapse text-left">
      <thead>
        <tr>
          {head.map((h) => (
            <th key={h} className="border-b border-line-strong bg-surface-2 px-1.5 py-1 text-[0.65rem] font-semibold uppercase tracking-wide">{h}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} className="break-inside-avoid">
            {r.map((c, j) => (
              <td key={j} className="border-b border-line px-1.5 py-1 align-top">{c}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function PrintReport({
  report,
  event,
  status,
  displayClass,
  trace,
}: {
  report: ReportJson;
  event: IncidentEvent;
  status: "draft" | "signed";
  displayClass: EventClass;
  trace: SensorTrace | null;
}) {
  const h = report.header;
  const s = report.sign_off;
  const domain = windowDomain(event.window_pre_s, event.window_post_s);
  const f = (v: number | null, u: string, d = 1) => (v == null ? "—" : `${v.toFixed(d)} ${u}`);

  return (
    <article className="mx-auto max-w-[190mm] space-y-3 bg-surface p-6 text-[0.8rem] leading-snug print:p-0" data-testid="print-report">
      <header className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 text-[0.65rem] font-extrabold tracking-[0.18em]">
            <span className="inline-block h-3 w-3 rounded-sm bg-accent" aria-hidden /> ARGUS
          </div>
          <h1 className="mt-1 text-xl font-bold tracking-tight">Incident / near-miss investigation report</h1>
          <div className="num text-[1rem] font-semibold">{h.report_no}</div>
        </div>
        <div className="flex flex-col items-end gap-1">
          <StatusChip status={status} />
          <ClassChip value={displayClass} />
          <RiskChip score={report.risk.score} />
        </div>
      </header>
      {status === "draft" && (
        <p className="rounded-sm border border-near-miss-line bg-near-miss-bg px-2 py-1 font-semibold text-near-miss-fg">
          DRAFT &mdash; not yet signed off by a manager.
        </p>
      )}

      <Sec n={1} title="Header">
        <dl className="grid grid-cols-4 gap-x-4 gap-y-1.5">
          {[
            ["Occurred", <Time key="o" iso={h.occurred_at} />],
            ["Site", h.site],
            ["Location on site", h.location_on_site],
            ["Worker", h.worker],
            ["Crew", h.crew],
            ["Supervisor", h.supervisor],
            ["Device", h.device],
          ].map(([k, v], i) => (
            <div key={i}>
              <dt className="field-label">{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      </Sec>

      <Sec n={2} title="Summary">
        <p className="whitespace-pre-wrap">{report.summary}</p>
      </Sec>

      <Sec n={3} title="Sequence of events">
        <Table
          head={["Time (rel. to t0)", "Event", "Source"]}
          rows={report.sequence_of_events.map((e) => [<span key="t" className="num whitespace-nowrap">{fmtOffset(e.offset_s)}</span>, e.description, <span key="s" className="capitalize">{e.source}</span>])}
        />
      </Sec>

      <Sec n={4} title="Evidence">
        <div className="space-y-2">
          <Table head={["Clip time", "Observation"]} rows={report.evidence.clip_refs.map((c) => [<span key="t" className="num whitespace-nowrap">{fmtOffset(c.offset_s)}</span>, c.observation])} />
          <Table head={["Key sensor value", "Value"]} rows={report.evidence.key_sensor_values.map((k) => [k.name, <span key="v" className="num">{k.value}</span>])} />
          <p><span className="field-label">Environment: </span>{report.evidence.environment}</p>
        </div>
      </Sec>

      <Sec n={5} title="Hazard identification">
        <p>
          <span className="field-label">Category: </span>{label(HAZARD_OPTIONS, report.hazard.category)}
          <span className="field-label ml-4">Energy source: </span>{report.hazard.energy_source}
        </p>
      </Sec>

      <Sec n={6} title="Risk assessment">
        <div className="flex gap-4">
          <div className="min-w-0 flex-1 space-y-1">
            <p>
              <span className="field-label">Severity: </span>
              <span className="num">{report.risk.severity}</span> {SEVERITY_LABELS[report.risk.severity - 1]}. {report.risk.severity_justification}
            </p>
            <p>
              <span className="field-label">Likelihood: </span>
              <span className="num">{report.risk.likelihood}</span> {LIKELIHOOD_LABELS[report.risk.likelihood - 1]}. {report.risk.likelihood_justification}
            </p>
            <p className="font-semibold">Risk score <span className="num">{report.risk.score}</span> / 25</p>
          </div>
          <RiskMatrix severity={report.risk.severity} likelihood={report.risk.likelihood} />
        </div>
      </Sec>

      <Sec n={7} title="Causes">
        <Table head={["Immediate cause", "Kind"]} rows={report.causes.immediate.map((c) => [c.description, label(CAUSE_KIND_OPTIONS, c.kind)])} />
        <div className="field-label mb-0.5 mt-2">Root cause: 5 whys</div>
        <ol className="list-decimal pl-5">
          {report.causes.root_five_whys.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ol>
      </Sec>

      <Sec n={8} title="Corrective and preventive actions">
        <Table
          head={["Action", "Control level", "Suggested owner role"]}
          rows={report.corrective_actions.map((a) => [a.description, label(CONTROL_OPTIONS, a.control_level), a.suggested_owner_role])}
        />
      </Sec>

      <Sec n={9} title="Regulatory note">
        <p className="whitespace-pre-wrap">{report.regulatory_note}</p>
      </Sec>

      <Sec n={10} title="Confidence and gaps">
        <p className="whitespace-pre-wrap">{report.confidence_and_gaps}</p>
      </Sec>

      <Sec n={11} title="Sensor snapshot">
        {trace ? (
          <div data-testid="print-chart">
            <SensorChart trace={trace} domain={domain} width={720} panelHeight={60} className="h-auto w-full" />
          </div>
        ) : (
          <p className="text-muted">Sensor trace not available for this event.</p>
        )}
        <dl className="mt-2 grid grid-cols-6 gap-2">
          {[
            ["Peak accel", fmtPeakG(event.peak_g, event.peak_g_saturated)],
            ["Closing rate", f(event.closing_rate_mps, "m/s")],
            ["Time to contact", f(event.ttc_s, "s")],
            ["Min range", f(event.range_m, "m")],
            ["Sensor", event.sensor_id ?? "—"],
            ["Hat on", event.hat_on == null ? "—" : event.hat_on ? "Yes" : "No"],
          ].map(([k, v]) => (
            <div key={k}>
              <dt className="field-label">{k}</dt>
              <dd className="num font-semibold">{v}</dd>
            </div>
          ))}
        </dl>
      </Sec>

      <Sec n={12} title="Sign-off">
        <div className="grid grid-cols-2 gap-6" data-testid="print-signoff">
          <dl className="space-y-1">
            <div><dt className="field-label">Final classification</dt><dd>{s.final_class ? <ClassChip value={s.final_class} /> : "—"}</dd></div>
            <div><dt className="field-label">AI reclassification</dt><dd className="capitalize">{(s.reclass_decision ?? "not applicable").replace("_", " ")}</dd></div>
          </dl>
          <div className="space-y-3">
            <div>
              <div className="min-h-[1.6rem] border-b border-ink pb-0.5 font-semibold">{s.signed_by ?? ""}</div>
              <div className="field-label">Signed by</div>
            </div>
            <div>
              <div className="num min-h-[1.2rem] border-b border-ink pb-0.5">{s.signed_at ? <Time iso={s.signed_at} /> : ""}</div>
              <div className="field-label">Date and time</div>
            </div>
          </div>
        </div>
      </Sec>

      <footer className="border-t border-line-strong pt-2 text-[0.7rem] text-muted">{REPORT_FOOTER}</footer>
    </article>
  );
}
