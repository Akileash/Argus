import { defineConfig } from "@playwright/test";

// Run: pnpm exec playwright test -c tests/e2e/playwright.config.ts   (needs local Supabase + seed)
export default defineConfig({
  // These specs share one live database, and some insert events the others count.
  workers: 1,
  testDir: ".",
  testMatch: /.*\.spec\.ts/,
  use: { baseURL: process.env.WEB_BASE_URL ?? "http://localhost:3000", viewport: { width: 1440, height: 900 } },
  webServer: process.env.WEB_BASE_URL
    ? undefined
    : { command: "pnpm dev", url: "http://localhost:3000/login", reuseExistingServer: true, timeout: 120_000, cwd: "../.." },
});
