import { defineConfig } from "@playwright/test";

export default defineConfig({
  // These specs share one live database, and some insert events the others count.
  workers: 1,
  testDir: ".",
  testMatch: ["e2e/**/*.spec.ts", "tests/e2e/**/*.spec.ts"],
  use: { baseURL: process.env.WEB_BASE_URL ?? "http://localhost:3000" },
  webServer: process.env.WEB_BASE_URL
    ? undefined
    : { command: "pnpm dev", url: "http://localhost:3000/login", reuseExistingServer: true, timeout: 120_000 },
});
