import { notFound } from "next/navigation";
import { AutoPrint } from "@/components/incident/auto-print";
import { PrintReport } from "@/components/incident/print-report";
import { getIncident } from "@/lib/queries/incident";
import { getSessionContext } from "@/lib/session";

export const dynamic = "force-dynamic";
export const metadata = { title: "Argus report" };

/** Print-styled report: no app chrome. Signed-in users only; data is RLS-scoped. */
export default async function PrintPage({ params }: { params: Promise<{ id: string }> }) {
  await getSessionContext(); // redirects to /login when signed out
  const { id } = await params;
  const data = await getIncident(id);
  if (!data) notFound();
  if (!data.report) {
    return <p className="p-8 text-sm text-muted">This event has no report to print.</p>;
  }
  return (
    <>
      <style>{`@page { size: auto; margin: 12mm; } @media print { body { background: #fff !important; } }`}</style>
      <AutoPrint />
      <PrintReport
        report={data.report.report_json}
        event={data.event}
        status={data.report.status}
        displayClass={data.event.final_class ?? data.event.device_class}
        trace={data.trace}
        noVideo={data.noVideo}
      />
    </>
  );
}
