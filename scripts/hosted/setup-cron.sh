#!/usr/bin/env bash
# Prepare the hosted worker safety net (supabase/hosted/cron.sql) for your deployed URL.
#
#   scripts/hosted/setup-cron.sh https://your-app.vercel.app
#   scripts/hosted/setup-cron.sh https://your-app.vercel.app --secret-file /path/to/existing/secret.txt
#
# What it does:
#   1. makes a private temp directory (mode 700) and a strong random secret in it (secret.txt, mode 600),
#      or reuses --secret-file so you can change the URL without rotating the secret;
#   2. fills supabase/hosted/cron.sql with the URL and the secret into <dir>/cron.filled.sql (mode 600);
#   3. prints the two commands you run next. The secret is NEVER printed or passed on a command line:
#      it goes to Vercel from the file through stdin, and to Supabase inside the SQL file.
#
# Afterwards delete the directory it names (the secret is now in Vercel and in Supabase Vault).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEMPLATE="$ROOT/supabase/hosted/cron.sql"

usage() {
  sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit "${1:-2}"
}

APP_URL=""
SECRET_FILE=""
while [ $# -gt 0 ]; do
  case "$1" in
    -h | --help) usage 0 ;;
    --secret-file)
      [ $# -ge 2 ] || usage
      SECRET_FILE="$2"
      shift 2
      ;;
    -*)
      echo "unknown option: $1" >&2
      usage
      ;;
    *)
      [ -z "$APP_URL" ] || usage
      APP_URL="$1"
      shift
      ;;
  esac
done
[ -n "$APP_URL" ] || usage

APP_URL="${APP_URL%/}"
# https only, plain host characters only: the value goes into SQL text, so nothing else is allowed through.
if ! [[ "$APP_URL" =~ ^https://[A-Za-z0-9.-]+(:[0-9]+)?$ ]]; then
  echo "error: <app-url> must look like https://your-app.vercel.app (https, host only, no path)" >&2
  exit 2
fi
if [ ! -f "$TEMPLATE" ]; then
  echo "error: template not found: $TEMPLATE" >&2
  exit 1
fi

umask 077
OUT="$(mktemp -d "${TMPDIR:-/tmp}/argus-cron.XXXXXX")"

if [ -n "$SECRET_FILE" ]; then
  [ -f "$SECRET_FILE" ] || {
    echo "error: --secret-file not found: $SECRET_FILE" >&2
    exit 2
  }
  SECRET="$(tr -d '\r\n' <"$SECRET_FILE")"
else
  command -v openssl >/dev/null 2>&1 || {
    echo "error: openssl is needed to generate the secret" >&2
    exit 1
  }
  SECRET="$(openssl rand -hex 32)"
fi
if ! [[ "$SECRET" =~ ^[A-Za-z0-9_-]{32,}$ ]]; then
  echo "error: the secret must be at least 32 characters of [A-Za-z0-9_-]" >&2
  exit 2
fi
printf '%s' "$SECRET" >"$OUT/secret.txt"

# Pure bash substitution: the secret never appears in a process argument list.
TPL="$(cat "$TEMPLATE")"
TPL="${TPL//__APP_URL__/$APP_URL}"
TPL="${TPL//__WORKER_CRON_SECRET__/$SECRET}"
if [[ "$TPL" == *"__APP_URL__"* || "$TPL" == *"__WORKER_CRON_SECRET__"* ]]; then
  echo "error: placeholders left in the filled SQL" >&2
  exit 1
fi
printf '%s\n' "$TPL" >"$OUT/cron.filled.sql"
unset SECRET TPL

cat <<EOF
Prepared in: $OUT   (private; contains the secret; delete it when you are done)

Step 1. Give the secret to Vercel (read from the file on stdin, so it is never typed or shown):

    cd $ROOT
    pnpm dlx vercel env add WORKER_CRON_SECRET production < "$OUT/secret.txt"
    # then redeploy so the running app has it:  pnpm dlx vercel deploy --prebuilt --prod   (or promote a new build)

Step 2. Install the cron job in Supabase (project must be linked: pnpm exec supabase link --project-ref <ref>):

    pnpm exec supabase db query --linked -f "$OUT/cron.filled.sql"

  or, with a database connection string in \$DATABASE_URL:

    psql "\$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$OUT/cron.filled.sql"

Step 3. Check it (SQL editor or db query):

    select jobname, schedule, active from cron.job where jobname = 'argus-worker-run';
    select status, return_message from cron.job_run_details order by start_time desc limit 3;

Step 4. Delete the directory:

    rm -r "$OUT"

Turn the job off later:  pnpm exec supabase db query --linked -f supabase/hosted/cron-off.sql
EOF
