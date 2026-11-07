import { notFound } from "next/navigation";
import { IncidentView } from "@/components/incident/incident-view";
import { getIncident } from "@/lib/queries/incident";
import { getSessionContext } from "@/lib/session";

export const dynamic = "force-dynamic";

export default async function IncidentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [ctx, data] = await Promise.all([getSessionContext(), getIncident(id)]);
  if (!data) notFound();
  return <IncidentView data={data} role={ctx.role} fullName={ctx.fullName} />;
}
