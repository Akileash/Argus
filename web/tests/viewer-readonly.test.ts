import { beforeEach, describe, expect, it, vi } from "vitest";
import { makeReport } from "@argus/shared";

/**
 * Viewer accounts (judges) are read-only. The database enforces it (supabase/tests/viewer_readonly.test.sql); these
 * tests check the server actions / routes refuse a viewer up front and never even try to write.
 */
const m = vi.hoisted(() => ({ role: "viewer" as "viewer" | "manager" | null, writes: [] as string[] }));

vi.mock("next/cache", () => ({ revalidatePath: () => {} }));
vi.mock("@/lib/supabase/admin", () => ({ createAdminClient: () => ({}) }));
vi.mock("@/lib/supabase/server", () => ({
  createClient: async () => ({
    auth: { getUser: async () => ({ data: { user: { id: "u-1" } } }) },
    from: (table: string) => ({
      select: () => ({
        eq: () => ({ maybeSingle: async () => ({ data: m.role ? { role: m.role, org_id: "org-1" } : null }) }),
      }),
      update: () => {
        m.writes.push(`update ${table}`);
        return { eq: () => ({ eq: () => ({ select: async () => ({ data: [], error: null }) }) }) };
      },
      insert: () => {
        m.writes.push(`insert ${table}`);
        return Promise.resolve({ error: null });
      },
    }),
    rpc: async (name: string) => {
      m.writes.push(`rpc ${name}`);
      return { data: null, error: { code: "42501", message: "nope" } };
    },
  }),
}));

import { saveReport, signOffReport } from "@/app/(app)/incidents/[id]/actions";
import { assignDeviceWorker } from "@/app/(app)/devices/actions";
import { POST as registerDevice } from "@/app/api/devices/register/route";

beforeEach(() => {
  m.role = "viewer";
  m.writes = [];
});

describe("viewer role is read-only", () => {
  it("saveReport refuses and writes nothing", async () => {
    const r = await saveReport("rep-1", makeReport());
    expect(r).toMatchObject({ ok: false });
    expect((r as { error: string }).error).toMatch(/read-only/i);
    expect(m.writes).toEqual([]);
  });

  it("signOffReport refuses and neither saves nor signs", async () => {
    const r = await signOffReport({ reportId: "rep-1", report: makeReport(), reclassDecision: null });
    expect(r).toMatchObject({ ok: false });
    expect((r as { error: string }).error).toMatch(/read-only/i);
    expect(m.writes).toEqual([]);
  });

  it("assignDeviceWorker refuses a viewer", async () => {
    const r = await assignDeviceWorker("dev-1", "worker-1");
    expect(r).toEqual({ ok: false, error: "Only managers can assign workers." });
    expect(m.writes).toEqual([]);
  });

  it("an account with no profile is treated as read-only too", async () => {
    m.role = null;
    const r = await saveReport("rep-1", makeReport());
    expect(r).toMatchObject({ ok: false });
    expect(m.writes).toEqual([]);
  });

  it("POST /api/devices/register is 403 for a viewer", async () => {
    const res = await registerDevice(new Request("http://app.test/api/devices/register", { method: "POST", body: JSON.stringify({ serial: "ARG-9" }) }));
    expect(res.status).toBe(403);
  });

  it("a manager is not blocked by the guard (it reaches the database write)", async () => {
    m.role = "manager";
    await saveReport("rep-1", makeReport());
    expect(m.writes).toContain("update reports");
  });
});
