import { HomeLive } from "@/components/home/home-live";
import { EventToaster } from "@/components/realtime/event-toaster";
import { createClient } from "@/lib/supabase/server";
import { getSessionContext } from "@/lib/session";
import { fetchHomeData, toKpis, type HomeData } from "@/lib/queries/home";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const ctx = await getSessionContext();
  const supabase = await createClient();

  let data: HomeData = { rows: [], kpis: toKpis(null), devices: [] };
  let error: string | null = null;
  try {
    data = await fetchHomeData(supabase);
  } catch (e) {
    error = e instanceof Error ? e.message : "Unknown error";
  }

  return (
    <EventToaster orgId={ctx.orgId}>
      <HomeLive orgId={ctx.orgId} initialRows={data.rows} initialKpis={data.kpis} initialDevices={data.devices} initialError={error} />
    </EventToaster>
  );
}
