import { afterEach, describe, expect, it, vi } from "vitest";

describe("dev-only demo login hint", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("is off in production and on in development", async () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.resetModules();
    expect((await import("@/lib/env")).isDev).toBe(false);

    vi.stubEnv("NODE_ENV", "development");
    vi.resetModules();
    expect((await import("@/lib/env")).isDev).toBe(true);
  });
});
