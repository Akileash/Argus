import path from "node:path";
import { expect, test } from "@playwright/test";

test("home: login, KPI row, seeded incident list, screenshot", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel(/email/i).fill("demo@argus.local");
  await page.getByLabel(/password/i).fill("argus-demo-2026");
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page).toHaveURL(/:\d+\/$/);

  await expect(page.getByTestId("kpi-row")).toBeVisible();
  await expect(page.getByTestId("kpi-near")).toBeVisible();
  await expect(page.getByTestId("kpi-incident")).toBeVisible();
  await expect(page.getByTestId("kpi-draft")).toBeVisible();
  await expect(page.getByTestId("device-list")).toContainText("ARG-0001");

  const rows = page.getByTestId("incident-row");
  await expect(rows.first()).toBeVisible();
  expect(await rows.count()).toBeGreaterThanOrEqual(12);
  await expect(page.getByTestId("live-badge")).toHaveAttribute("data-state", "live");

  // Class filter narrows the list.
  await page.getByRole("button", { name: /^PPE log/ }).click();
  const ppe = await rows.count();
  expect(ppe).toBeGreaterThan(0);
  for (const cls of await rows.evaluateAll((els) => els.map((e) => e.getAttribute("data-class")))) expect(cls).toBe("ppe_log");
  await page.getByRole("button", { name: /^All/ }).click();

  await page.screenshot({ path: path.join(__dirname, "__screenshots__", "home.png"), fullPage: true });
});

test("a wrong password keeps the typed email", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel(/email/i).fill("demo@argus.local");
  await page.getByLabel(/password/i).fill("not-the-password");
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page.getByText("Invalid email or password.")).toBeVisible();
  await expect(page.getByLabel(/email/i)).toHaveValue("demo@argus.local");
});

test("redirects stay on the address the visitor opened", async ({ page, baseURL }) => {
  // On a laptop's IP address, a redirect to localhost loses the session cookie and strands the visitor.
  const origin = new URL(baseURL!).origin;
  await page.goto("/devices");
  await expect(page).toHaveURL(`${origin}/login`);
  await page.getByLabel(/email/i).fill("demo@argus.local");
  await page.getByLabel(/password/i).fill("argus-demo-2026");
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page.getByTestId("kpi-row")).toBeVisible();
  await page.goto("/devices");
  await expect(page).toHaveURL(`${origin}/devices`);
  await page.getByRole("button", { name: /sign out/i }).click();
  await expect(page).toHaveURL(`${origin}/login`);
});
