import { beforeEach, describe, expect, it, vi } from "vitest";

const m = vi.hoisted(() => ({
  afterFns: [] as Array<() => unknown>,
  afterThrows: false,
  completeResult: { status: "uploaded" } as { status: string },
  drain: vi.fn(async () => ({ processed: 1, remaining: 0 })),
}));

vi.mock("next/server", async (orig) => ({
  ...(await orig<typeof import("next/server")>()),
  after: (fn: () => unknown) => {
    if (m.afterThrows) throw new Error("after() called outside a request scope");
    m.afterFns.push(fn);
  },
}));
vi.mock("@/lib/api/device-auth", () => ({ authenticateDevice: async () => ({ id: "dev-1", org_id: "org-1" }) }));
vi.mock("@/lib/supabase/admin", () => ({ createAdminClient: () => ({}) }));
vi.mock("@/lib/api/events", () => ({ completeEvent: async () => m.completeResult }));
vi.mock("@/lib/worker/deps", () => ({ createWorkerDeps: () => ({ cfg: {}, store: {} }) }));
vi.mock("@/lib/worker/process", () => ({ drainEvents: m.drain }));

import * as route from "@/app/api/events/[id]/complete/route";

const ID = "6f1d2c1a-3b4e-4f5a-8b6c-7d8e9f0a1b2c";
const call = () =>
  route.POST(
    new Request(`http://app.test/api/events/${ID}/complete`, {
      method: "POST",
      headers: { authorization: "Bearer t", "content-type": "application/json" },
      body: JSON.stringify({ uploaded: ["front.mp4"] }),
    }),
    { params: Promise.resolve({ id: ID }) },
  );

beforeEach(() => {
  m.afterFns.length = 0;
  m.afterThrows = false;
  m.completeResult = { status: "uploaded" };
  m.drain.mockClear();
  m.drain.mockResolvedValue({ processed: 1, remaining: 0 });
  delete process.env.WORKER_INLINE;
});

describe("POST /api/events/[id]/complete", () => {
  it("allows the after() work to run for up to 300 s", () => {
    expect(route.maxDuration).toBe(300);
  });

  it("answers first and schedules processing with after(), without running it inline", async () => {
    const res = await call();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ status: "uploaded" });
    expect(m.afterFns).toHaveLength(1);
    expect(m.drain).not.toHaveBeenCalled(); // nothing ran before the response
    await m.afterFns[0]!();
    expect(m.drain).toHaveBeenCalledTimes(1);
  });

  it("a failing report run cannot surface in the device's request or the background task", async () => {
    m.drain.mockRejectedValue(new Error("gemini down"));
    const res = await call();
    expect(res.status).toBe(200);
    await expect(Promise.resolve(m.afterFns[0]!())).resolves.toBeUndefined();
  });

  it("a failure to schedule does not fail the device's request", async () => {
    m.afterThrows = true;
    const res = await call();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ status: "uploaded" });
  });

  it("does not schedule for already-short-circuited events (hat_removed / module_fallback are `ready`)", async () => {
    for (const status of ["ready", "analyzing", "failed"]) {
      m.completeResult = { status };
      const res = await call();
      expect(res.status).toBe(200);
    }
    expect(m.afterFns).toHaveLength(0);
  });

  it("WORKER_INLINE=off leaves processing to the standalone worker process", async () => {
    process.env.WORKER_INLINE = "off";
    await call();
    expect(m.afterFns).toHaveLength(0);
  });
});
