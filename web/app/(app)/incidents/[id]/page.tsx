// TODO(incident-detail agent): evidence column (video + synced sensor chart), report form, sign-off, progress stepper.
export default async function IncidentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <section>
      <h1 className="text-xl font-bold tracking-tight">
        Incident <span className="num">{id}</span>
      </h1>
      <p className="mt-1 text-sm text-muted">TODO: incident detail and report.</p>
    </section>
  );
}
