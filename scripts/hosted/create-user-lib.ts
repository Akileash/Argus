/**
 * Logic for scripts/hosted/create-user.ts, kept free of I/O so it can be tested with a mocked admin client.
 * Nothing in here prints or returns a password or a key.
 */
import type { SupabaseClient } from "@supabase/supabase-js";

export type Role = "manager" | "viewer";

export interface CreateUserArgs {
  email: string;
  name: string;
  role: Role;
  org: string;
  resetPassword: boolean;
}

export const MIN_PASSWORD_LENGTH = 10;

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** Parse `--email x --name "Y" --role manager|viewer [--org Argus] [--reset-password]` (also `--flag=value`). Throws on bad input. */
export function parseArgs(argv: string[]): CreateUserArgs {
  const vals: Record<string, string> = {};
  const flags = new Set<string>();
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]!;
    if (!a.startsWith("--")) throw new Error(`unexpected argument: ${a}`);
    const [k, inline] = a.slice(2).split(/=(.*)/s, 2) as [string, string | undefined];
    if (k === "reset-password") {
      flags.add(k);
      continue;
    }
    if (!["email", "name", "role", "org"].includes(k)) throw new Error(`unknown option: --${k}`);
    const v = inline ?? argv[++i];
    if (v === undefined || v.startsWith("--")) throw new Error(`--${k} needs a value`);
    vals[k] = v;
  }
  const email = (vals.email ?? "").trim().toLowerCase();
  if (!EMAIL_RE.test(email)) throw new Error("--email must be a valid email address");
  const name = (vals.name ?? "").trim();
  if (!name) throw new Error("--name is required (the person's full name, shown as the signer on reports)");
  const role = vals.role;
  if (role !== "manager" && role !== "viewer") throw new Error("--role must be manager or viewer");
  const org = (vals.org ?? "Argus").trim();
  if (!org) throw new Error("--org must not be empty");
  return { email, name, role, org, resetPassword: flags.has("reset-password") };
}

export interface Io {
  /** Ask for a password with input hidden. Resolves to the typed value. */
  promptPassword(label: string): Promise<string>;
  confirm(question: string): Promise<boolean>;
  log(msg: string): void;
}

export interface Result {
  outcome: "created" | "exists";
  userId: string;
  passwordReset: boolean;
  profile: "created" | "updated" | "unchanged";
}

async function findUserByEmail(admin: SupabaseClient, email: string): Promise<{ id: string } | null> {
  for (let page = 1; page <= 50; page++) {
    const { data, error } = await admin.auth.admin.listUsers({ page, perPage: 200 });
    if (error) throw new Error(`could not list users: ${error.message}`);
    const hit = data.users.find((u) => (u.email ?? "").toLowerCase() === email);
    if (hit) return { id: hit.id };
    if (data.users.length < 200) return null;
  }
  return null;
}

async function askNewPassword(io: Io): Promise<string> {
  for (let tries = 0; tries < 3; tries++) {
    const a = await io.promptPassword("Password");
    if (a.length < MIN_PASSWORD_LENGTH) {
      io.log(`Password must be at least ${MIN_PASSWORD_LENGTH} characters. Try again.`);
      continue;
    }
    const b = await io.promptPassword("Repeat password");
    if (a !== b) {
      io.log("The two passwords differ. Try again.");
      continue;
    }
    return a;
  }
  throw new Error("no valid password entered");
}

/**
 * Idempotent: creates the confirmed auth user and the profile, or finds them. If the user exists it offers to reset
 * the password (or does so with --reset-password), and makes the profile (name, role, org) match the arguments.
 */
export async function createUser(admin: SupabaseClient, args: CreateUserArgs, io: Io): Promise<Result> {
  const orgs = await admin.from("organizations").select("id").eq("name", args.org);
  if (orgs.error) throw new Error(`could not look up organization: ${orgs.error.message}`);
  if (!orgs.data || orgs.data.length === 0) {
    throw new Error(`organization "${args.org}" does not exist. Run supabase/hosted/setup.sql first.`);
  }
  if (orgs.data.length > 1) throw new Error(`more than one organization is named "${args.org}"`);
  const orgId = (orgs.data[0] as { id: string }).id;

  const existing = await findUserByEmail(admin, args.email);
  let userId: string;
  let outcome: Result["outcome"];
  let passwordReset = false;

  if (!existing) {
    const password = await askNewPassword(io);
    const created = await admin.auth.admin.createUser({
      email: args.email,
      password,
      email_confirm: true,
      user_metadata: { full_name: args.name },
    });
    if (created.error || !created.data.user) throw new Error(`could not create the user: ${created.error?.message ?? "no user returned"}`);
    userId = created.data.user.id;
    outcome = "created";
    io.log(`Created confirmed user ${args.email}.`);
  } else {
    userId = existing.id;
    outcome = "exists";
    io.log(`User ${args.email} already exists.`);
    const reset = args.resetPassword || (await io.confirm("Reset the password?"));
    if (reset) {
      const password = await askNewPassword(io);
      const upd = await admin.auth.admin.updateUserById(userId, { password, email_confirm: true });
      if (upd.error) throw new Error(`could not reset the password: ${upd.error.message}`);
      passwordReset = true;
      io.log("Password reset.");
    }
  }

  const prof = await admin.from("profiles").select("org_id, full_name, role").eq("user_id", userId).maybeSingle();
  if (prof.error) {
    if (outcome === "created") await admin.auth.admin.deleteUser(userId);
    throw new Error(`could not read the profile: ${prof.error.message}`);
  }
  let profile: Result["profile"];
  if (!prof.data) {
    const ins = await admin.from("profiles").insert({ user_id: userId, org_id: orgId, full_name: args.name, role: args.role });
    if (ins.error) {
      // do not leave a login without a profile behind (it could not use the app and a re-run would find it half-made)
      if (outcome === "created") await admin.auth.admin.deleteUser(userId);
      throw new Error(`could not create the profile: ${ins.error.message}`);
    }
    profile = "created";
    io.log(`Profile created: ${args.name}, ${args.role}, org ${args.org}.`);
  } else {
    const p = prof.data as { org_id: string; full_name: string; role: Role };
    if (p.org_id === orgId && p.full_name === args.name && p.role === args.role) {
      profile = "unchanged";
      io.log(`Profile already matches (${args.name}, ${args.role}, org ${args.org}).`);
    } else {
      const upd = await admin.from("profiles").update({ org_id: orgId, full_name: args.name, role: args.role }).eq("user_id", userId);
      if (upd.error) throw new Error(`could not update the profile: ${upd.error.message}`);
      profile = "updated";
      io.log(`Profile updated${p.role !== args.role ? ` (role ${p.role} -> ${args.role})` : ""}.`);
    }
  }
  return { outcome, userId, passwordReset, profile };
}
