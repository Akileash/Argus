import path from "node:path";
import { expect, test } from "@playwright/test";

/**
 * Opens the struck-by incident created by `scripts/demo-e2e.sh` (real device -> API -> worker pipeline).
 * Run via `scripts/demo-e2e.sh --ui`, or by hand:
 *   WEB_BASE_URL=http://127.0.0.1:3400 E2E_INCIDENT_ID=<event id> pnpm exec playwright test -c tests/e2e/playwright.config.ts e2e-struck-by
 * Skipped unless E2E_INCIDENT_ID is set.
 */
const ID = process.env.E2E_INCIDENT_ID;
const SHOTS = path.resolve(__dirname, "__screenshots__");

test.describe("live-stack struck-by incident", () => {
  test.skip(!ID, "E2E_INCIDENT_ID not set (run scripts/demo-e2e.sh --ui)");
  test.use({ viewport: { width: 1440, height: 1000 } });

  test("video loads, sensor chart and worker-written report render", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel(/email/i).fill("demo@argus.local");
    await page.getByLabel(/password/i).fill("argus-demo-2026");
    await page.getByRole("button", { name: /sign in/i }).click();
    await page.waitForURL((u) => !u.pathname.startsWith("/login"));

    await page.goto(`/incidents/${ID}`);
    await expect(page.getByTestId("incident-detail")).toBeVisible();

    const video = page.getByTestId("incident-video");
    await expect(video).toBeVisible();
    const src = await video.evaluate((v: HTMLVideoElement) => v.currentSrc || v.src);
    expect(src, "video has a source").toMatch(/^https?:/);
    await expect
      .poll(() => video.evaluate((v: HTMLVideoElement) => v.readyState), { timeout: 20_000 })
      .toBeGreaterThan(0);
    const dur = await video.evaluate((v: HTMLVideoElement) => v.duration);
    expect(dur).toBeGreaterThan(25);
    expect(dur).toBeLessThan(35);

    await expect(page.getByTestId("sensor-chart")).toBeVisible();
    await expect(page.getByTestId("t0-marker")).toBeVisible();
    await expect(page.getByTestId("no-trace")).toHaveCount(0);
    await expect(page.getByTestId("no-clip")).toHaveCount(0);

    await expect(page.getByTestId("report-form")).toBeVisible();
    await expect(page.getByTestId("report-no")).toHaveText(/^ARG-\d{4}-\d{4}$/);
    await expect(page.getByRole("textbox", { name: "Location on site" })).toHaveValue("Location to be determined");
    await expect(page.getByTestId("report-footer")).toBeVisible();

    await page.screenshot({ path: path.join(SHOTS, "e2e-struck-by.png"), fullPage: true });
  });
});
