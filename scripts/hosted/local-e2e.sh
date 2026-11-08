#!/usr/bin/env bash
# Local end-to-end check of the hosted design: NO worker process. The app (production build, REPORT_MODE=fixture)
# makes the report itself through after() and through POST /api/worker/run.
#
#   scripts/hosted/local-e2e.sh [--reset] [--port N] [--no-build]
#
# Needs local Supabase (pnpm exec supabase start). --reset runs `supabase db reset` first (LOCAL data only).
# Keys come from `supabase status` of the LOCAL stack and are never printed.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/opt/postgresql@16/bin:/opt/homebrew/bin:$PATH"
RESET=0
BUILD=1
PORT=3450
while [ $# -gt 0 ]; do
  case "$1" in
    --reset) RESET=1 ;;
    --no-build) BUILD=0 ;;
    --port)
      shift
      PORT="${1:-3450}"
      ;;
    -h | --help)
      sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      exit 2
      ;;
  esac
  shift
done

cd "$ROOT" || exit 2
pnpm exec supabase status >/dev/null 2>&1 || {
  echo "Local Supabase is not running (pnpm exec supabase start)." >&2
  exit 2
}
if [ "$RESET" = 1 ]; then
  pnpm exec supabase db reset >/dev/null 2>&1 || {
    echo "supabase db reset failed" >&2
    exit 2
  }
fi

STATUS_ENV="$(pnpm exec supabase status -o env 2>/dev/null)"
sbval() { printf '%s\n' "$STATUS_ENV" | sed -n "s/^$1=\"\{0,1\}\([^\"]*\)\"\{0,1\}$/\1/p" | head -1; }
NEXT_PUBLIC_SUPABASE_URL="$(sbval API_URL)"
NEXT_PUBLIC_SUPABASE_ANON_KEY="$(sbval ANON_KEY)"
SUPABASE_SERVICE_ROLE_KEY="$(sbval SERVICE_ROLE_KEY)"
export NEXT_PUBLIC_SUPABASE_URL NEXT_PUBLIC_SUPABASE_ANON_KEY SUPABASE_SERVICE_ROLE_KEY
export REPORT_MODE=fixture
WORKER_CRON_SECRET="e2e-$(openssl rand -hex 16)"
export WORKER_CRON_SECRET
export ARGUS_INTEGRATION=1
export WEB_BASE_URL="http://127.0.0.1:$PORT"

LOG="$(mktemp -d)/web.log"
if [ "$BUILD" = 1 ] || [ ! -d web/.next ]; then
  (cd web && pnpm exec next build) >"$LOG.build" 2>&1 || {
    tail -20 "$LOG.build" >&2
    exit 2
  }
fi
(cd web && exec pnpm exec next start -p "$PORT" -H 127.0.0.1) >"$LOG" 2>&1 &
WEB_PID=$!
# shellcheck disable=SC2329  # invoked via trap
cleanup() { kill "$WEB_PID" 2>/dev/null; }
trap cleanup EXIT
for _ in $(seq 1 60); do
  curl -fsS -o /dev/null "http://127.0.0.1:$PORT/login" 2>/dev/null && break
  sleep 0.5
done

echo "no worker process is running; web log: $LOG"
(cd web && pnpm exec vitest run tests/integration/hosted-worker.int.test.ts)
rc=$?
echo "---- app log lines from the in-app worker"
grep '\[worker\]' "$LOG" | tail -n 12
exit "$rc"
