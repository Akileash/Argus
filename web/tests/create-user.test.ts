import { beforeEach, describe, expect, it } from "vitest";
import type { SupabaseClient } from "@supabase/supabase-js";
import { createUser, parseArgs, type Io } from "../../scripts/hosted/create-user-lib";

interface AuthUser { id: string; email: string; password?: string }
interface Profile { user_id: string; org_id: string; full_name: string; role: string }

/** Mock of the slice of the admin client the script uses. */
function mockAdmin(init: { orgs?: { id: string; name: string }[]; users?: AuthUser[]; profiles?: Profile[]; failProfileInsert?: boolean } = {}) {
  const st = {
    orgs: init.orgs ?? [{ id: "org-argus", name: "Argus" }],
    users: init.users ?? [],
    profiles: init.profiles ?? [],
    deleted: [] as string[],
    calls: [] as string[],
  };
  let seq = 0;
  const admin = {
    auth: {
      admin: {
        listUsers: async () => ({ data: { users: st.users }, error: null }),
        createUser: async (a: { email: string; password: string; email_confirm: boolean; user_metadata: unknown }) => {
          st.calls.push(`createUser confirm=${a.email_confirm}`);
          const u = { id: `u-${++seq}`, email: a.email, password: a.password };
          st.users.push(u);
          return { data: { user: u }, error: null };
        },
        updateUserById: async (id: string, a: { password?: string }) => {
          st.calls.push("updateUserById");
          const u = st.users.find((x) => x.id === id)!;
          if (a.password) u.password = a.password;
          return { data: { user: u }, error: null };
        },
        deleteUser: async (id: string) => {
          st.deleted.push(id);
          st.users = st.users.filter((u) => u.id !== id);
          return { error: null };
        },
      },
    },
    from: (table: string) => {
      if (table === "organizations") {
        return { select: () => ({ eq: async (_c: string, name: string) => ({ data: st.orgs.filter((o) => o.name === name), error: null }) }) };
      }
      return {
        select: () => ({ eq: (_c: string, id: string) => ({ maybeSingle: async () => ({ data: st.profiles.find((p) => p.user_id === id) ?? null, error: null }) }) }),
        insert: async (row: Profile) => {
          st.calls.push("insert profile");
          if (init.failProfileInsert) return { error: { message: "boom" } };
          st.profiles.push(row);
          return { error: null };
        },
        update: (patch: Partial<Profile>) => ({
          eq: async (_c: string, id: string) => {
            st.calls.push("update profile");
            Object.assign(st.profiles.find((p) => p.user_id === id)!, patch);
            return { error: null };
          },
        }),
      };
    },
  };
  return { admin: admin as unknown as SupabaseClient, st };
}

function io(passwords: string[], confirms: boolean[] = []): Io & { logs: string[] } {
  const logs: string[] = [];
  return {
    logs,
    promptPassword: async () => passwords.shift() ?? "",
    confirm: async () => confirms.shift() ?? false,
    log: (m) => logs.push(m),
  };
}

const ARGS = { email: "judge@example.com", name: "Judge One", role: "viewer" as const, org: "Argus", resetPassword: false };
const PW = "correct-horse-battery";

describe("parseArgs", () => {
  it("parses the documented flags and lowercases the email", () => {
    expect(parseArgs(["--email", "A@B.com", "--name", "Ann Lee", "--role", "manager"])).toEqual({
      email: "a@b.com", name: "Ann Lee", role: "manager", org: "Argus", resetPassword: false,
    });
    expect(parseArgs(["--email=a@b.com", "--name=Ann", "--role=viewer", "--org=Other", "--reset-password"])).toMatchObject({ org: "Other", resetPassword: true });
  });
  it("rejects missing or bad input, and any attempt to pass a password", () => {
    expect(() => parseArgs(["--name", "A", "--role", "viewer"])).toThrow(/email/);
    expect(() => parseArgs(["--email", "not-an-email", "--name", "A", "--role", "viewer"])).toThrow(/email/);
    expect(() => parseArgs(["--email", "a@b.com", "--role", "viewer"])).toThrow(/name/);
    expect(() => parseArgs(["--email", "a@b.com", "--name", "A", "--role", "admin"])).toThrow(/role/);
    expect(() => parseArgs(["--email", "a@b.com", "--name", "A", "--role", "viewer", "--password", "x"])).toThrow(/unknown option/);
  });
});

describe("createUser", () => {
  let m: ReturnType<typeof mockAdmin>;
  beforeEach(() => {
    m = mockAdmin();
  });

  it("creates a confirmed auth user and the profile", async () => {
    const r = await createUser(m.admin, ARGS, io([PW, PW]));
    expect(r).toMatchObject({ outcome: "created", profile: "created", passwordReset: false });
    expect(m.st.calls).toContain("createUser confirm=true");
    expect(m.st.users).toHaveLength(1);
    expect(m.st.users[0]!.password).toBe(PW);
    expect(m.st.profiles).toEqual([{ user_id: r.userId, org_id: "org-argus", full_name: "Judge One", role: "viewer" }]);
  });

  it("never logs the password", async () => {
    const i = io([PW, PW]);
    await createUser(m.admin, ARGS, i);
    expect(i.logs.join("\n")).not.toContain(PW);
  });

  it("re-asks on a short password or a mismatch", async () => {
    const i = io(["short", PW, "different-pass-1", PW, PW]);
    await createUser(m.admin, ARGS, i);
    expect(m.st.users[0]!.password).toBe(PW);
    expect(i.logs.some((l) => /at least/.test(l))).toBe(true);
    expect(i.logs.some((l) => /differ/.test(l))).toBe(true);
  });

  it("is idempotent: a second run changes nothing and does not ask for a password", async () => {
    await createUser(m.admin, ARGS, io([PW, PW]));
    const i = io([], [false]);
    const r = await createUser(m.admin, ARGS, i);
    expect(r).toMatchObject({ outcome: "exists", profile: "unchanged", passwordReset: false });
    expect(m.st.users).toHaveLength(1);
    expect(m.st.profiles).toHaveLength(1);
  });

  it("offers a password reset for an existing user, and resets it when accepted", async () => {
    await createUser(m.admin, ARGS, io([PW, PW]));
    const r = await createUser(m.admin, ARGS, io(["a-brand-new-password", "a-brand-new-password"], [true]));
    expect(r.passwordReset).toBe(true);
    expect(m.st.users[0]!.password).toBe("a-brand-new-password");
    expect(m.st.users).toHaveLength(1);
  });

  it("--reset-password resets without a question", async () => {
    await createUser(m.admin, ARGS, io([PW, PW]));
    const r = await createUser(m.admin, { ...ARGS, resetPassword: true }, io(["another-long-password", "another-long-password"]));
    expect(r.passwordReset).toBe(true);
  });

  it("repairs a user that has a login but no profile, and updates a changed role", async () => {
    m = mockAdmin({ users: [{ id: "u-x", email: "judge@example.com" }] });
    const r = await createUser(m.admin, ARGS, io([], [false]));
    expect(r).toMatchObject({ outcome: "exists", profile: "created", userId: "u-x" });
    const r2 = await createUser(m.admin, { ...ARGS, role: "manager" }, io([], [false]));
    expect(r2.profile).toBe("updated");
    expect(m.st.profiles[0]!.role).toBe("manager");
  });

  it("fails clearly when the organization does not exist, before creating anything", async () => {
    await expect(createUser(m.admin, { ...ARGS, org: "Nope" }, io([PW, PW]))).rejects.toThrow(/setup\.sql/);
    expect(m.st.users).toHaveLength(0);
  });

  it("removes the new login again when the profile cannot be created", async () => {
    m = mockAdmin({ failProfileInsert: true });
    await expect(createUser(m.admin, ARGS, io([PW, PW]))).rejects.toThrow(/profile/);
    expect(m.st.users).toHaveLength(0);
    expect(m.st.deleted).toHaveLength(1);
  });
});
