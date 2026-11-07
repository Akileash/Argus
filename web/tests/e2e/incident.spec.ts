import { execFileSync } from "node:child_process";
import path from "node:path";
import { randomUUID } from "node:crypto";
import { createClient } from "@supabase/supabase-js";
import { expect, test, type Page } from "@playwright/test";

/**
 * Incident detail e2e against local Supabase (pnpm exec supabase start; web/.env.local).
 * Needs seeded event ...0001 with media attached: `pnpm exec tsx scripts/dev-attach-media.ts`.
 * The sign-off test works on a throwaway CLONE of that event so the seed stays unsigned; the clone is
 * removed at the end through psql (signed reports cannot be deleted by the API).
 */
try {
  process.loadEnvFile(path.resolve(__dirname, "../../.env.local"));
} catch {
  // env may be provided by the caller
}

const SEED_EVENT = "00000000-0000-4000-8000-000000000001";
const SHOTS = path.resolve(__dirname, "__screenshots__");
const DB_URL = "postgresql://postgres:postgres@127.0.0.1:54322/postgres";

const admin = () => createClient(process.env.NEXT_PUBLIC_SUPABASE_URL!, process.env.SUPABASE_SERVICE_ROLE_KEY!, { auth: { persistSession: false } });

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel(/email/i).fill("demo@argus.local");
  await page.getByLabel(/password/i).fill("argus-demo-2026");
  await page.getByRole("button", { name: /sign in/i }).click();
  await page.waitForURL((u) => !u.pathname.startsWith("/login"));
}

/** Clone the seed draft (event + report) under a new id. Media is not cloned, so the clone also exercises the no-clip state. */
async function cloneSeedEvent(): Promise<{ id: string; reportNo: string }> {
  const sb = admin();
  const { data: ev } = await sb.from("events").select("*").eq("id", SEED_EVENT).single();
  const { data: rep } = await sb.from("reports").select("llm_draft_json, report_json").eq("event_id", SEED_EVENT).single();
  const { data: no } = await sb.rpc("next_report_no");
  const id = randomUUID();
  const { error } = await sb.from("events").insert({ ...ev, id, final_class: null, created_at: undefined, updated_at: undefined });
  if (error) throw error;
  const reportNo = no as string;
  const draft = structuredClone(rep!.llm_draft_json);
  draft.header.report_no = reportNo;
  const current = structuredClone(rep!.report_json);
  current.header.report_no = reportNo;
  const { error: e2 } = await sb.from("reports").insert({ event_id: id, report_no: reportNo, llm_draft_json: draft, report_json: current, status: "draft" });
  if (e2) throw e2;
  return { id, reportNo };
}

function dropClone(id: string) {
  try {
    execFileSync("psql", [DB_URL, "-q", "-c", `set session_replication_role = replica; delete from report_edits where report_id in (select id from reports where event_id='${id}'); delete from reports where event_id='${id}'; delete from media where event_id='${id}'; delete from events where id='${id}';`]);
  } catch {
    // psql unavailable: leave the clone
  }
}

test.describe("incident detail", () => {
  test.use({ viewport: { width: 1440, height: 1000 } });

  test("evidence: video and chart are synced both ways, t0 marker, env strip", async ({ page }) => {
    await login(page);
    await page.goto(`/incidents/${SEED_EVENT}`);
    await expect(page.getByTestId("incident-detail")).toBeVisible();
    await expect(page.getByTestId("reclass-banner")).toContainText("AI suggests");
    await expect(page.getByTestId("t0-marker")).toBeVisible();
    await expect(page.getByTestId("env-strip")).toContainText("biased by the wearer");

    const video = page.getByTestId("incident-video");
    await expect(video).toBeVisible();
    await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThan(0);
    await page.screenshot({ path: path.join(SHOTS, "incident-detail-full.png"), fullPage: true });

    // chart -> video: click the middle of the chart plot
    const chart = page.getByTestId("sensor-chart");
    const box = (await chart.boundingBox())!;
    await page.mouse.click(box.x + 46 + (box.width - 56) * (20 / 30), box.y + 60); // t0
    await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeCloseTo(20, 0);
    await expect(chart).toHaveAttribute("data-playhead", /^(-0\.\d+|0\.\d+|0\.000|-0\.000)$/);

    // drag on the chart
    await page.mouse.move(box.x + 46 + (box.width - 56) * 0.25, box.y + 60);
    await page.mouse.down();
    await page.mouse.move(box.x + 46 + (box.width - 56) * 0.5, box.y + 60, { steps: 5 });
    await page.mouse.up();
    await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeCloseTo(15, 0);

    // video -> chart: seek the video element, chart cursor must follow
    await video.evaluate((v: HTMLVideoElement) => {
      v.currentTime = 25;
    });
    await expect(chart).toHaveAttribute("data-playhead", /^5\.0/);
    await expect(page.getByTestId("playhead-readout")).toContainText("+5.00 s");

    // scrubber keyboard
    await page.getByTestId("scrubber").focus();
    await page.keyboard.press("ArrowLeft");
    await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeCloseTo(24.9, 1);
    await page.screenshot({ path: path.join(SHOTS, "incident-evidence.png"), clip: { x: 0, y: 140, width: 760, height: 900 } });
  });

  test("review: edit, mark as edited, view original, save, sign off, print", async ({ page }) => {
    const clone = await cloneSeedEvent();
    try {
      await login(page);
      await page.goto(`/incidents/${clone.id}`);
      await expect(page.getByTestId("report-no")).toHaveText(clone.reportNo);
      await expect(page.getByTestId("no-clip")).toBeVisible(); // graceful: no media on the clone
      await expect(page.getByTestId("no-trace")).toBeVisible();

      const summary = page.getByRole("textbox", { name: "Summary" });
      const original = await summary.inputValue();
      await expect(page.locator('[data-path="summary"]')).not.toHaveAttribute("data-edited", "true");
      await summary.fill(`${original} Edited by the safety manager.`);
      await expect(page.locator('[data-path="summary"]')).toHaveAttribute("data-edited", "true");
      await expect(page.locator('[data-path="summary"]')).toContainText("edited by manager");
      await page.locator('[data-path="summary"]').getByRole("button", { name: "View original" }).click();
      await expect(page.getByTestId("original-text")).toContainText(original.slice(0, 40));

      // risk score recomputes live
      await page.getByRole("combobox", { name: "Severity", exact: true }).selectOption("5");
      await page.getByRole("combobox", { name: "Likelihood", exact: true }).selectOption("4");
      await expect(page.getByTestId("risk-score")).toHaveAttribute("data-score", "20");
      await page.getByRole("textbox", { name: "Location on site" }).fill("Level 4, east stair");

      await page.getByTestId("save-report").click();
      await expect(page.getByTestId("save-msg")).toHaveText("Saved");
      await page.reload();
      await expect(page.getByRole("textbox", { name: "Summary" })).toHaveValue(/Edited by the safety manager\./);
      await expect(page.getByRole("textbox", { name: "Location on site" })).toHaveValue("Level 4, east stair");
      await page.screenshot({ path: path.join(SHOTS, "incident-report-edited.png"), fullPage: true });

      // reclass banner: choose Accept (pending until sign-off)
      await page.getByTestId("reclass-accept").click();
      await expect(page.getByTestId("reclass-state")).toContainText("Pending: you chose to accept");

      await page.getByTestId("signoff-open").click();
      await expect(page.getByTestId("signoff-dialog")).toBeVisible();
      await expect(page.getByTestId("signoff-name")).toHaveText("Dana Whitfield");
      await page.screenshot({ path: path.join(SHOTS, "incident-signoff-dialog.png") });
      await page.getByTestId("signoff-confirm").click();

      await expect(page.getByTestId("signed-banner")).toContainText("This report is locked");
      await expect(page.getByTestId("signoff-open")).toHaveCount(0);
      await expect(page.getByRole("textbox", { name: "Summary" })).toBeDisabled();
      await expect(page.getByTestId("signoff-block")).toContainText("Dana Whitfield");
      await page.screenshot({ path: path.join(SHOTS, "incident-signed.png"), fullPage: true });

      // DB: report locked, final class recorded
      const sb = admin();
      const { data: ev } = await sb.from("events").select("final_class").eq("id", clone.id).single();
      expect(ev!.final_class).toBe("incident");
      const { data: rep } = await sb.from("reports").select("status, signed_off_by, edited, report_json").eq("event_id", clone.id).single();
      expect(rep!.status).toBe("signed");
      expect(rep!.edited).toBe(true);
      expect(rep!.report_json.sign_off.reclass_decision).toBe("accepted");
      const locked = await sb.from("reports").update({ report_json: { ...rep!.report_json, summary: "x" } }).eq("event_id", clone.id).select();
      expect(locked.error?.code === "55006" || (locked.data ?? []).length === 0).toBe(true);

      // print view (no chrome)
      await page.goto(`/print/${clone.id}?noprint=1`);
      await expect(page.getByTestId("print-report")).toContainText(clone.reportNo);
      await expect(page.getByTestId("print-signoff")).toContainText("Dana Whitfield");
      await expect(page.getByText("AI-generated draft for review by a competent person").first()).toBeVisible();
      await expect(page.locator("header.no-print, nav")).toHaveCount(0);
      await page.emulateMedia({ media: "print" });
      await expect(page.getByTestId("print-button")).toBeHidden();
      await page.screenshot({ path: path.join(SHOTS, "incident-print-signed.png"), fullPage: true });
    } finally {
      dropClone(clone.id);
    }
  });

  test("print route renders the seeded draft with the sensor chart snapshot", async ({ page }) => {
    await login(page);
    await page.goto(`/print/${SEED_EVENT}?noprint=1`);
    await expect(page.getByTestId("print-chart").locator("svg")).toBeVisible();
    await expect(page.getByText("DRAFT").first()).toBeVisible();
    await page.emulateMedia({ media: "print" });
    await page.screenshot({ path: path.join(SHOTS, "incident-print-draft.png"), fullPage: true });
    const pdf = await page.pdf({ format: "A4", printBackground: true });
    expect(pdf.length).toBeGreaterThan(10_000);
  });

  test("processing and failed events show the stepper", async ({ page }) => {
    const sb = admin();
    // events ...0 'uploaded' rows exist in the seed/dev DB; fall back to a clone if none
    const { data: proc } = await sb.from("events").select("id, status").in("status", ["uploaded", "analyzing", "received", "uploading"]).limit(1);
    test.skip(!proc?.length, "no processing event in DB");
    await login(page);
    await page.goto(`/incidents/${proc![0].id}`);
    await expect(page.getByTestId("processing-stepper")).toBeVisible();
    await page.screenshot({ path: path.join(SHOTS, "incident-processing.png"), fullPage: true });
  });
});
