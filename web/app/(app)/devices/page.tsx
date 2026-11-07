import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getSessionContext } from "@/lib/session";
import { createClient } from "@/lib/supabase/server";
import { RegisterDeviceForm } from "./register-form";

export default async function DevicesPage() {
  const ctx = await getSessionContext();
  const supabase = await createClient();
  const { data: devices } = await supabase
    .from("device_last_seen")
    .select("device_id, serial, last_event_at, assigned_worker_name")
    .order("serial");

  return (
    <section className="grid gap-5 lg:grid-cols-[1fr_380px]">
      <div>
        <h1 className="mb-3 text-xl font-bold tracking-tight">Devices</h1>
        <Card className="overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-surface-2 text-left">
              <tr>
                <th className="field-label px-4 py-2">Serial</th>
                <th className="field-label px-4 py-2">Assigned worker</th>
                <th className="field-label px-4 py-2">Last event seen</th>
              </tr>
            </thead>
            <tbody>
              {(devices ?? []).map((d) => (
                <tr key={d.device_id} className="border-t border-line">
                  <td className="num px-4 py-2 font-medium">{d.serial}</td>
                  <td className="px-4 py-2">{d.assigned_worker_name ?? <span className="text-muted">Unassigned</span>}</td>
                  <td className="num px-4 py-2">
                    {d.last_event_at ? new Date(d.last_event_at).toISOString().replace("T", " ").slice(0, 19) + " UTC" : <span className="text-muted">never</span>}
                  </td>
                </tr>
              ))}
              {(devices ?? []).length === 0 && (
                <tr>
                  <td colSpan={3} className="px-4 py-6 text-center text-muted">No devices yet.</td>
                </tr>
              )}
            </tbody>
          </table>
        </Card>
      </div>
      <div>
        <h2 className="mb-3 text-xl font-bold tracking-tight">Pairing</h2>
        <Card>
          <CardHeader>
            <CardTitle>Pair a hat</CardTitle>
          </CardHeader>
          <CardContent>
            {ctx.role === "manager" ? (
              <RegisterDeviceForm orgId={ctx.orgId} />
            ) : (
              <p className="text-sm text-muted">Only managers can register devices.</p>
            )}
          </CardContent>
        </Card>
      </div>
    </section>
  );
}
