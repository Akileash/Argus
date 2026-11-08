/**
 * Create (or update) a dashboard login on the hosted Supabase project without any secret passing through chat.
 *
 *   export SUPABASE_URL=https://<ref>.supabase.co          # public, fine to type
 *   export SUPABASE_SERVICE_ROLE_KEY=...                   # secret: paste it into YOUR terminal only
 *   pnpm exec tsx scripts/hosted/create-user.ts --email judge@example.com --name "Judge One" --role viewer --org Argus
 *
 * The password is typed at a hidden prompt (a real terminal is required). It is never an argument, never read
 * from the environment, and never printed. Re-running for an existing email offers to reset the password.
 *   --role manager   can edit and sign reports, assign hats, pair devices
 *   --role viewer    read-only (for judges)
 * Optional: --reset-password (reset without being asked), --org <name> (default "Argus").
 */
import { createClient } from "@supabase/supabase-js";
import { createInterface } from "node:readline";
import { createUser, parseArgs, type Io } from "./create-user-lib";

/** Read one line from the terminal with echo off. */
function promptHidden(label: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const stdin = process.stdin;
    if (!stdin.isTTY || !process.stdout.isTTY) {
      reject(new Error("the password must be typed in a terminal (stdin is not a TTY); it is never read from args, env or pipes"));
      return;
    }
    process.stdout.write(`${label}: `);
    let buf = "";
    stdin.setRawMode(true);
    stdin.resume();
    stdin.setEncoding("utf8");
    const done = (fn: () => void) => {
      stdin.setRawMode(false);
      stdin.pause();
      stdin.removeListener("data", onData);
      process.stdout.write("\n");
      fn();
    };
    const onData = (chunk: string) => {
      for (const ch of chunk) {
        if (ch === "\r" || ch === "\n" || ch === "\u0004") return done(() => resolve(buf));
        if (ch === "\u0003") return done(() => reject(new Error("cancelled")));
        if (ch === "\u007f" || ch === "\b") buf = buf.slice(0, -1);
        else if (ch >= " ") buf += ch;
      }
    };
    stdin.on("data", onData);
  });
}

function ask(question: string): Promise<boolean> {
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  return new Promise((resolve) => {
    rl.question(`${question} [y/N] `, (a) => {
      rl.close();
      resolve(/^y(es)?$/i.test(a.trim()));
    });
  });
}

async function main(): Promise<void> {
  let args;
  try {
    args = parseArgs(process.argv.slice(2));
  } catch (e) {
    console.error(`error: ${(e as Error).message}\n`);
    console.error('usage: tsx scripts/hosted/create-user.ts --email <email> --name "<Full Name>" --role manager|viewer [--org Argus] [--reset-password]');
    process.exit(2);
  }
  const url = (process.env.SUPABASE_URL ?? process.env.NEXT_PUBLIC_SUPABASE_URL ?? "").trim();
  const key = (process.env.SUPABASE_SERVICE_ROLE_KEY ?? "").trim();
  if (!url || !key) {
    console.error("error: set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in your environment (the key is never printed).");
    process.exit(2);
  }
  if (!process.stdin.isTTY) {
    console.error("error: run this in an interactive terminal: the password is typed at a hidden prompt.");
    process.exit(2);
  }
  console.log(`Target project: ${new URL(url).host}`);
  const admin = createClient(url, key, { auth: { persistSession: false, autoRefreshToken: false } });
  const io: Io = { promptPassword: promptHidden, confirm: ask, log: (m) => console.log(m) };
  try {
    const r = await createUser(admin, args, io);
    console.log(`Done: ${r.outcome === "created" ? "user created" : "user already existed"}, profile ${r.profile}${r.passwordReset ? ", password reset" : ""}.`);
  } catch (e) {
    console.error(`error: ${(e as Error).message}`);
    process.exit(1);
  }
}

void main();
