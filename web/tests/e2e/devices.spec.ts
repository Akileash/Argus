import { execFileSync } from "node:child_process";
import { expect, test, type Page } from "@playwright/test";

const DB_URL = "postgresql://postgres:postgres@127.0.0.1:54322/postgres";

/**
 * Devices page, as the seeded demo manager: pairing command with copy button, and worker assignment.
 * Needs local Supabase + seed. The assignment test puts the seed back as it found it.
 */
/** Click Save and wait for the server action (a POST to /devices) to finish, so a reload sees the result. */
async function save(page: Page, serial: string) {
  await Promise.all([
    page.waitForResponse((r) => r.request().method() === "POST" && new URL(r.url()).pathname === "/devices"),
    page.getByTestId(`assign-save-${serial}`).click(),
  ]);
}

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel(/email/i).fill("demo@argus.local");
  await page.getByLabel(/password/i).fill("argus-demo-2026");
  await page.getByRole("button", { name: /sign in/i }).click();
  await page.waitForURL((u) => !u.pathname.startsWith("/login"));
}

test.describe("devices page", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  // the registration test creates a throwaway device; remove it so the demo Devices page stays clean
  test.afterAll(() => {
    execFileSync("psql", [DB_URL, "-c", "delete from public.devices where serial like 'ARG-T%'"]);
  });

  test("a manager can reassign a hat to another worker and back", async ({ page }) => {
    await login(page);
    await page.goto("/devices");
    const select = page.getByTestId("assign-select-ARG-0001");
    await expect(select).toBeVisible();
    const original = await select.inputValue();
    const options = await select.locator("option").evaluateAll((os) => os.map((o) => ({ v: (o as HTMLOptionElement).value, t: o.textContent ?? "" })));
    const other = options.find((o) => o.v && o.v !== original)!;
    expect(other, "a free worker is offered").toBeTruthy();

    await select.selectOption(other.v);
    await save(page, "ARG-0001");
    await page.reload();
    await expect(page.getByTestId("assign-select-ARG-0001")).toHaveValue(other.v);

    // restore
    await page.getByTestId("assign-select-ARG-0001").selectOption(original);
    await save(page, "ARG-0001");
    await page.reload();
    await expect(page.getByTestId("assign-select-ARG-0001")).toHaveValue(original);
  });

  test("a worker who wears another hat is not offered", async ({ page }) => {
    await login(page);
    await page.goto("/devices");
    const taken = await page.getByTestId("assign-select-ARG-0002").inputValue();
    const options = await page.getByTestId("assign-select-ARG-0001").locator("option").evaluateAll((os) => os.map((o) => (o as HTMLOptionElement).value));
    expect(taken).not.toBe("");
    expect(options).not.toContain(taken);
  });

  test("registering shows the exact pairing command with a copy button", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await login(page);
    await page.goto("/devices");
    await page.getByLabel("Serial").fill(`ARG-T${Date.now() % 100000}`);
    await page.getByRole("button", { name: /register device/i }).click();
    const cmd = page.getByTestId("pair-command");
    await expect(cmd).toBeVisible();
    const token = (await page.getByTestId("device-token").textContent())!.trim();
    await expect(cmd).toHaveText(`.venv/bin/argus-cli pair --url ${new URL(page.url()).origin} --token ${token} --config /etc/argus/config.yaml`);
    await page.getByTestId("copy-pair-command").click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe((await cmd.textContent())!.trim());
    await expect(page.getByText(/must be reachable from the Pi/i)).toBeVisible();
  });
});

test("a serial typed while the page is still loading is kept", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel(/email/i).fill("demo@argus.local");
  await page.getByLabel(/password/i).fill("argus-demo-2026");
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page.getByTestId("kpi-row")).toBeVisible();
  await page.goto("/devices", { waitUntil: "commit" });
  const serial = `ARG-H${Date.now() % 100000}`;
  await page.getByLabel("Serial").fill(serial); // as early as the field exists, before hydration
  await page.waitForLoadState("networkidle");
  await expect(page.getByLabel("Serial")).toHaveValue(serial);
  await page.getByRole("button", { name: /register device/i }).click();
  await expect(page.getByTestId("pair-command")).toBeVisible();
});
