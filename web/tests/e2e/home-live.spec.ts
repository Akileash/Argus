import path from "node:path";
import { randomUUID, createHash } from "node:crypto";
import { expect, test } from "@playwright/test";

// Drives the real local Supabase with the service role while the page is open.
const URL = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "http://127.0.0.1:54321";
const SERVICE =
  process.env.SUPABASE_SERVICE_ROLE_KEY ??
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImV4cCI6MTk4MzgxMjk5Nn0.EGIM96RAZx35lJzdJsyH-qQwv8Hdp7fsn3W0YpN81IU";

async function rest(method: string, p: string, body?: unknown, prefer = "return=representation") {
  const res = await fetch(`${URL}/rest/v1/${p}`, {
    method,
    headers: { apikey: SERVICE, Authorization: `Bearer ${SERVICE}`, "Content-Type": "application/json", Prefer: prefer },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${method} ${p}: ${res.status} ${await res.text()}`);
  return res.status === 204 ? null : res.json();
}

test("live: inserted event animates in, toast flips to ready", async ({ page }) => {
  const id = randomUUID();
  const deviceId = createHash("md5").update("device-2").digest("hex").replace(/(.{8})(.{4})(.{4})(.{4})(.{12})/, "$1-$2-$3-$4-$5");
  try {
    await page.goto("/login");
    await page.getByLabel(/email/i).fill("demo@argus.local");
    await page.getByLabel(/password/i).fill("argus-demo-2026");
    await page.getByRole("button", { name: /sign in/i }).click();
    await expect(page.getByTestId("live-badge")).toHaveAttribute("data-state", "live");
    const before = await page.getByTestId("incident-row").count();
    const kpiBefore = Number(await page.getByTestId("kpi-near").innerText());

    const now = new Date().toISOString();
    await rest("POST", "events", { id, device_id: deviceId, type: "struck_by_near_miss", t0: now, detected_at: now, device_class: "near_miss", status: "received" });

    await expect(page.getByTestId("incident-row")).toHaveCount(before + 1);
    await expect(page.getByTestId("incident-row").first()).toHaveAttribute("data-event-id", id);
    await expect(page.getByRole("region", { name: "Notifications" })).toContainText("Report generating");
    await expect(page.getByTestId("kpi-near")).toHaveText(String(kpiBefore + 1));

    await rest("PATCH", `events?id=eq.${id}`, { status: "analyzing" });
    await expect(page.getByTestId("incident-row").first()).toContainText(/analyzing/i);

    const reportNo = await rest("POST", "rpc/next_report_no", {});
    await rest("POST", "reports", { event_id: id, report_no: reportNo, llm_draft_json: {}, report_json: {} });
    await rest("PATCH", `events?id=eq.${id}`, { status: "ready" });
    await expect(page.getByRole("region", { name: "Notifications" })).toContainText("Report ready");
    await expect(page.getByTestId("incident-row").first()).toContainText(reportNo);
    await page.screenshot({ path: path.join(__dirname, "__screenshots__", "home-live.png") });
  } finally {
    await rest("DELETE", `events?id=eq.${id}`, undefined, "return=minimal");
  }
});
