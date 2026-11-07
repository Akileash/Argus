// Visual polish screenshots: pnpm exec tsx scripts/polish-shots.ts <before|after> [baseURL]
import path from "node:path";
import { chromium } from "@playwright/test";

const prefix = process.argv[2] ?? "after";
const base = process.argv[3] ?? "http://localhost:3200";
const OUT = path.resolve(__dirname, "../tests/e2e/__screenshots__/polish");
const EV = "00000000-0000-4000-8000-000000000001";
const sizes = [
  { n: "1440", w: 1440, h: 900 },
  { n: "1920", w: 1920, h: 1080 },
];

(async () => {
  const browser = await chromium.launch();
  for (const s of sizes) {
    const ctx = await browser.newContext({ viewport: { width: s.w, height: s.h } });
    const page = await ctx.newPage();
    const shot = async (name: string, full = true) => {
      await page.waitForTimeout(700);
      await page.screenshot({ path: path.join(OUT, `${prefix}-${name}-${s.n}.png`), fullPage: full });
    };
    await page.goto(`${base}/login`);
    await shot("login", false);
    await page.getByLabel(/email/i).fill("demo@argus.local");
    await page.getByLabel(/password/i).fill("argus-demo-2026");
    await page.getByRole("button", { name: /sign in/i }).click();
    await page.waitForURL((u) => !u.pathname.startsWith("/login"));
    await shot("home");
    await page.goto(`${base}/incidents/${EV}`);
    await shot("incident");
    await page.goto(`${base}/devices`);
    await shot("devices");
    await page.goto(`${base}/print/${EV}`);
    await shot("print");
    await ctx.close();
  }
  await browser.close();
})();
