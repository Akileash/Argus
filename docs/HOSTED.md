# Hosted deployment (Vercel + Supabase cloud, no laptop)

The dashboard, the device API and the report worker all run on **Vercel (Hobby, free)**. The database, file storage and
logins run on **Supabase cloud (free)**. The Pi talks to the Vercel URL over the internet. Nothing has to stay on.

```
Pi hat --HTTPS--> Vercel  /api/events, /api/events/[id]/complete   (device API)
                     |  after the response: the report is written in the same function (after())
                     |  safety net: Supabase pg_cron -> POST /api/worker/run every minute
Judges / managers --> Vercel dashboard --> Supabase (Postgres, Storage, Auth)
```

Video and sensor files go from the Pi straight to Supabase Storage through signed URLs, so Vercel's 4.5 MB request limit
never applies to them.

Supabase project: `qlbudlqvzajtxsbogjyu`. Its 5 migrations are already applied. There is no seed (and there must not be
one: `supabase/seed.sql` is the demo data).

## 0. What you need

- A Vercel account (Hobby) and a Supabase account that owns the project.
- Node 22 and pnpm. In the repo root: `pnpm install`.
- The two Supabase keys (Dashboard > Project Settings > API Keys): the **anon / publishable** key and the
  **service_role / secret** key. Treat the service_role key like a password: type or paste it into your own terminal or the
  Vercel dashboard only. Never paste it into chat, a commit or a ticket.
- Optional, for real reports: `GEMINI_API_KEY` (and `ANTHROPIC_API_KEY` for hybrid mode).

## 1. Deploy, in order

Every command runs from the repo root.

### 1.1 Supabase: link and load the production setup data

```bash
pnpm exec supabase login
pnpm exec supabase link --project-ref qlbudlqvzajtxsbogjyu
pnpm exec supabase db query --linked -f supabase/hosted/setup.sql
```

`setup.sql` creates exactly: org "Argus", site "natHacks" (Edmonton, AB), crew "natHacks Crew" (supervisor Dip Khadka),
worker "Okitha Biyanwila", and device `ARG-0001` assigned to Okitha. Nothing else. It is idempotent: run it again any
time. (No `supabase` CLI link? Use `psql "<database connection string>" -v ON_ERROR_STOP=1 -f supabase/hosted/setup.sql`.)

### 1.2 Supabase: turn public sign-up off (dashboard)

See "Auth hardening" below. Do it now, before the URL is public.

### 1.3 Vercel: create the project (once)

```bash
pnpm dlx vercel login
pnpm dlx vercel link          # create a new project, e.g. "argus"; keep the defaults
```

Then in the Vercel dashboard, **Project > Settings**:

| Setting | Value |
| --- | --- |
| General > Root Directory | `web` |
| General > Include source files outside of the Root Directory | **on** (the app imports `packages/shared`, `worker/` and `contracts/`) |
| General > Node.js Version | 22.x |
| Functions > **Fluid Compute** | **on** (Hobby needs it for a 300 s `maxDuration`; without it the limit is 60 s) |
| Build and Development Settings | leave the defaults. `web/vercel.json` sets the install command (`cd .. && pnpm install --frozen-lockfile`) and `next build`. |

### 1.4 Vercel: environment variables

Add each one for the **Production** environment. Run the command and type or paste the value at the prompt (it is not
echoed into logs), or use Project > Settings > Environment Variables.

```bash
pnpm dlx vercel env add NEXT_PUBLIC_SUPABASE_URL production        # https://qlbudlqvzajtxsbogjyu.supabase.co
pnpm dlx vercel env add NEXT_PUBLIC_SUPABASE_ANON_KEY production
pnpm dlx vercel env add SUPABASE_SERVICE_ROLE_KEY production       # mark "Sensitive" in the dashboard
pnpm dlx vercel env add REPORT_MODE production                     # fixture | gemini | hybrid
# only when REPORT_MODE is gemini or hybrid:
pnpm dlx vercel env add GEMINI_API_KEY production
pnpm dlx vercel env add GEMINI_MODEL production                    # e.g. gemini-3.5-flash
pnpm dlx vercel env add ANTHROPIC_API_KEY production               # hybrid only
pnpm dlx vercel env add ANTHROPIC_MODEL production                 # hybrid only, e.g. claude-opus-5-5
pnpm dlx vercel env add LLM_SPEND_CAP_USD production               # e.g. 10
```

`WORKER_CRON_SECRET` comes in step 1.6. The full list, with what is secret, is in "Environment variables" below.
Start with `REPORT_MODE=fixture` to prove the whole path works with no keys, then switch to `gemini` or `hybrid`.

### 1.5 Vercel: build locally and deploy the prebuilt output

```bash
pnpm dlx vercel pull --yes --environment production
pnpm dlx vercel build --prod
pnpm dlx vercel deploy --prebuilt --prod
```

The last command prints the production URL. Open `/login` to check it loads. (Building locally means the build uses your
pnpm, not Vercel's.)

### 1.6 The cron safety net

The report normally starts by itself the moment the Pi finishes uploading. The cron is the backup for the rare case it did
not (the function was cut off, the model was slow, a deploy happened mid-report). Use the final URL from 1.5:

```bash
scripts/hosted/setup-cron.sh https://<your-app>.vercel.app
```

It makes a private temp folder with a new random secret and a filled copy of `supabase/hosted/cron.sql`, and prints the two
commands to run. In order:

```bash
# 1. give the secret to Vercel (read from the file; never shown)
pnpm dlx vercel env add WORKER_CRON_SECRET production < "<temp folder>/secret.txt"
# 2. install the job in Supabase (pg_cron + pg_net; the URL and secret are stored in Supabase Vault)
pnpm exec supabase db query --linked -f "<temp folder>/cron.filled.sql"
# 3. redeploy so the app sees the new variable
pnpm dlx vercel pull --yes --environment production && pnpm dlx vercel build --prod && pnpm dlx vercel deploy --prebuilt --prod
# 4. delete the temp folder
```

Check it:

```bash
curl -i -X POST https://<your-app>.vercel.app/api/worker/run          # 401 (no secret)
```

and in the Supabase SQL editor: `select status, return_message from cron.job_run_details order by start_time desc limit 5;`
then `select status_code, left(content::text, 100) from net._http_response order by created desc limit 5;` (expect 200 and
`{"processed":0,"remaining":0}`). Turn the job off any time with `supabase/hosted/cron-off.sql`.

### 1.7 Create the accounts

On your own machine, with the keys in your own shell (the password is typed at a hidden prompt, never as an argument):

```bash
export SUPABASE_URL=https://qlbudlqvzajtxsbogjyu.supabase.co
export SUPABASE_SERVICE_ROLE_KEY=...       # paste in your terminal only
pnpm exec tsx scripts/hosted/create-user.ts --email you@example.com  --name "Dip Khadka" --role manager --org Argus
pnpm exec tsx scripts/hosted/create-user.ts --email judge@example.com --name "Judge"      --role viewer  --org Argus
```

- **manager**: edits and signs reports, assigns hats, pairs devices.
- **viewer**: read-only, for judges. The database refuses every write from a viewer (row-level security), and the app also
  shows "read-only" before trying. Tested in `supabase/tests/viewer_readonly.test.sql`.
- Run it again for an existing email: it offers to reset the password and fixes the profile. Several judges: run it once each.
- `unset SUPABASE_SERVICE_ROLE_KEY` afterwards.

### 1.8 Pair the Pi

1. Sign in as the manager, open **Devices**, register serial `ARG-0001` (it already exists from `setup.sql`, unpaired).
   Copy the token (shown once).
2. On the Pi:

```bash
cd ~/argus/device
.venv/bin/argus-cli pair --url https://<your-app>.vercel.app --token <token> --config /etc/argus/config.yaml
.venv/bin/argus-cli pair --check --config /etc/argus/config.yaml
```

No trailing slash, no `/api`. More in [CONNECT-THE-HAT.md](CONNECT-THE-HAT.md) (option B).

### 1.9 Smoke test

Trigger an event on the hat (or run the device demo against the URL). Within seconds the dashboard shows the report as
`ready`. With `REPORT_MODE=fixture` it is instant; with a real model expect 20 to 60 s.

## 2. Environment variables (Vercel, Production)

| Variable | Secret? | Required | Value / notes |
| --- | --- | --- | --- |
| `NEXT_PUBLIC_SUPABASE_URL` | no | yes | `https://qlbudlqvzajtxsbogjyu.supabase.co`. Also used as the worker's `SUPABASE_URL`. |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | no (public by design; RLS protects the data) | yes | anon / publishable key. |
| `SUPABASE_SERVICE_ROLE_KEY` | **yes** | yes | service_role / secret key. Bypasses RLS: device API and worker only. |
| `WORKER_CRON_SECRET` | **yes** | yes | from `setup-cron.sh`. Without it `/api/worker/run` answers 401 to everyone (the fast path still works). |
| `REPORT_MODE` | no | yes | `fixture` (no keys), `gemini`, or `hybrid`. |
| `GEMINI_API_KEY` | **yes** | gemini, hybrid | |
| `GEMINI_MODEL` | no | gemini, hybrid | e.g. `gemini-3.5-flash`. |
| `ANTHROPIC_API_KEY` | **yes** | hybrid | |
| `ANTHROPIC_MODEL` | no | hybrid | default `claude-opus-5-5`. |
| `LLM_SPEND_CAP_USD` | no | recommended | default 25. At the cap the worker stops paid calls and writes sensor-only reports. |
| `LLM_CALL_TIMEOUT_S` | no | optional | hosted default **60** (the standalone worker's is 180). |
| `GEMINI_PROCESSING_TIMEOUT_S` | no | optional | hosted default **45** (standalone 120). |
| `GEMINI_TRANSIENT_RETRIES` | no | optional | hosted default **2** (standalone 3). |
| `GEMINI_TRANSIENT_BACKOFF_MS` | no | optional | hosted default **5000** (standalone 10000). |
| `WORKER_EVENT_BUDGET_S` | no | optional | hosted default **150**: wall-clock limit for the paid part of one event. |
| `WORKER_STALE_ANALYZING_MS` | no | optional | hosted default **360000**: an event `analyzing` with no heartbeat this long is re-queued. |
| `WORKER_HEARTBEAT_MS` | no | optional | hosted default 20000. |
| `WORKER_MAX_ANALYSIS_ATTEMPTS` | no | optional | default 3: claims per event, then it is marked `failed`. |
| `WORKER_MIN_CLIP_BYTES` | no | optional | default 50000: smaller clips count as "no video". |
| `WORKER_INLINE` | no | optional | `off` disables the in-app `after()` run (only for a standalone worker process). Default on. |

The "hosted default" values apply automatically when the variable is unset or blank, so you can leave them out.

## 3. Time budget: why one event always fits in 300 s

Vercel Hobby (Fluid Compute) allows `maxDuration = 300` s. Both routes that run the worker (`/api/worker/run` and
`/api/events/[id]/complete`, whose `after()` work shares its limit) declare it.

The slow part of an event is the model calls. Without a limit, one Gemini call alone can take
upload 60 + processing 45 + status check 30 + generate 60 = **195 s**, and a transient retry adds another 66 s, which is
**261 s** for one stage; hybrid adds a Claude call and a possible Gemini fallback. So the hosted worker uses a
**per-event wall-clock budget**, `WORKER_EVENT_BUDGET_S = 150`, measured from the moment the event is claimed:

- no new model call starts when less than 2 s of the budget is left; a running call is cut at the budget;
- in hybrid mode the Gemini stage also leaves one Claude call (60 s) in reserve, so Claude can still write the report;
- when the budget is used up the event does **not** fail: the report is written from the sensor data alone, with the reason
  in "confidence and gaps" (the same fallback as any video timeout).

Worst case for ONE event:

| Part | Seconds |
| --- | --- |
| Paid model stages, cut at the budget | 150 |
| Reads, trace and clip download, writes, a second try after an error (assumed, not measured) | 60 |
| **Worst case** | **210** |
| `maxDuration` | 300 |
| Margin | **90** (30%) |

How the loop uses it (`web/lib/worker/process.ts`):

- The first event always runs. Another one starts only if elapsed time is under **240 s** and `elapsed + worst case` is at most
  **285 s** (300 minus a 15 s margin). With real models that means a second event starts only if the first finished within
  75 s; the rest wait for the next cron tick (one minute). With `REPORT_MODE=fixture` (worst case 30 s) it drains up to 240 s.
- A heartbeat refreshes `events.updated_at` every 20 s while an event is worked on.
- **Reclaim:** an event stuck in `analyzing` with no heartbeat for **360 s** is re-queued. That is above the 300 s function limit
  and above the 210 s worst case, so a live event is never taken from a running function; only one whose function was killed.
- **Attempt cap:** 3 claims per event (reclaims count), then it is marked `failed` with a message. Spend cap and the
  sensor-only fallbacks are unchanged.
- `hat_removed` and `module_fallback` events are `ready` at ingest. They never reach the worker.

## 4. Auth hardening

- **Turn off public sign-up.** The app has no sign-up page, but the Supabase Auth API accepts sign-ups from anyone who has
  the (public) anon key unless you switch it off. A stranger's account would see nothing (row-level security scopes all data
  to a profile's org) but should not exist. Dashboard: **Authentication > Sign In / Providers** (older UI: Authentication >
  Providers > Email) > turn **"Allow new users to sign up"** off. Accounts are created by `create-user.ts`, which uses the
  admin API and works with sign-up off.
- Authentication > URL Configuration: set **Site URL** to `https://<your-app>.vercel.app`.
- Keep **Anonymous sign-ins** off (the default).
- The demo login hint on `/login` shows only when `NODE_ENV` is not `production`; Vercel builds are production, and a test
  (`web/tests/prod-login.test.ts`) plus a check of the built page confirm it is absent.
- The local file `supabase/config.toml` has `enable_signup = true` for the local stack. That only affects local Docker.

## 5. Worker logs

- Vercel dashboard > your project > **Logs**. Filter by path `/api/worker/run` (cron runs) or search `[worker]`. Lines look
  like `[worker] event <id> ready: ARG-2026-0001 (near_miss, risk 9)` and `[worker] drain: processed=1 remaining=0 elapsed=3s`.
  Fast-path runs appear under the `/api/events/<id>/complete` request.
- CLI: `pnpm dlx vercel logs <deployment-url>` streams live logs.
- Hobby keeps runtime logs for a short time (about an hour), so for anything older use the database: `analyses` (one row per
  model call: tokens, cost, latency, output), `events.error` (why an event failed) and `events.analysis_attempts`.
- Cron side: `cron.job_run_details` and `net._http_response` in Supabase (see 1.6).

## 6. Roll back

- **App:** Vercel dashboard > Deployments > the previous good one > **Promote to Production** (or "Instant Rollback"), or
  `pnpm dlx vercel rollback` (previous production deployment) / `pnpm dlx vercel promote <deployment-url>`. On Hobby, instant
  rollback goes back to the previous production deployment.
- **Env var change:** edit it, then redeploy (variables are read at build and deploy time).
- **Stop the safety net:** `pnpm exec supabase db query --linked -f supabase/hosted/cron-off.sql`.
- **Stop all report processing:** set `WORKER_INLINE=off`, run `cron-off.sql`, redeploy. Events stay `uploaded` and are picked up
  when you turn it back on.
- **Re-run a failed event:** `update events set status = 'uploaded', analysis_attempts = 0, error = null where id = '<id>';`
  The next cron tick processes it.
- **Database:** migrations only go forward. Do not edit applied migrations; add a new one.

## 7. Free-tier limits and what to do

- **Supabase pauses a free project after about a week of inactivity.** Symptoms: the dashboard errors, the Pi's uploads fail
  with 5xx. Fix: Supabase dashboard > the project > **Restore project**. A project paused for less than 90 days restores with all
  data. Pings from the cron job may count as activity, but do not rely on it: open the project before a demo.
- **Storage: 1 GB** on the free plan (and a 50 MB limit per file). At about 5 to 15 MB per clip that is on the order of 100
  events. Delete old clips in Storage when it fills. **Database: 500 MB**, **egress: 5 GB** per month.
- **Vercel Hobby:** personal, non-commercial use; 300 s functions only with Fluid Compute; function invocations and active-CPU
  hours are capped monthly. The cron costs about 1,440 short invocations a day, far inside the limits.
- **Gemini / Anthropic:** your own quotas apply. `LLM_SPEND_CAP_USD` is a hard stop across all events.

## 8. Checks that ran before this was handed over

`scripts/check.sh` passes. `scripts/hosted/local-e2e.sh` starts the production build with `REPORT_MODE=fixture` and **no worker
process**, uploads an event through the real API and confirms: `/complete` answers immediately, the report appears through
`after()`, `/api/worker/run` answers 401 without the secret and re-processes an event left in `uploaded` with it, and
`hat_removed` never reaches the worker. `vercel build --prod` was run locally against a placeholder project setting (no
account was touched).
