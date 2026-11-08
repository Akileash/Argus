#!/usr/bin/env bash
# One-command mock end-to-end demo (M6.1).
#
#   hat (mock hardware, real device stack) -> uploader -> web API -> Supabase -> worker -> report
#
# Runs the demo scenarios (a fall and a hat removal; add --with-struck-by for the board swing) against local Supabase, a production build of the web
# app and the report worker, then asserts the resulting database/storage state through the REST API.
#
# Usage: scripts/demo-e2e.sh [--reset] [--keep-running] [--ui] [--with-struck-by] [--port N] [--no-build]
#   --reset         `supabase db reset` first (wipes local data, re-seeds)
#   --keep-running  leave web + worker up afterwards so you can watch the dashboard; Ctrl-C to stop
#   --port N        web port (default: first free port from 3400)
#   --ui            also open the fall incident in Playwright, save web/tests/e2e/__screenshots__/e2e-struck-by.png
#   --with-struck-by  also run the board-swing struck-by near miss (off by default)
#   --no-build      reuse the existing web/.next build
# Env: REPORT_MODE (default fixture), ARGUS_DEMO_SERIAL (default ARG-0002), ARGUS_DEMO_TOKEN
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/opt/postgresql@16/bin:/opt/homebrew/bin:$PATH"

RESET=0
KEEP=0
UI=0
STRUCK=0
BUILD=1
PORT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --reset) RESET=1 ;;
    --keep-running) KEEP=1 ;;
    --ui) UI=1 ;;
    --with-struck-by) STRUCK=1 ;;
    --no-build) BUILD=0 ;;
    --port) shift; PORT="${1:-}" ;;
    -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
  shift
done

SERIAL="${ARGUS_DEMO_SERIAL:-ARG-0002}"
TOKEN="${ARGUS_DEMO_TOKEN:-seed-token-ARG-0002}"
DEMO_DIR="$ROOT/device/var/demo"
LOG_DIR="$ROOT/device/var/demo-logs"
PIDS=()

log() { printf '\033[1m[demo]\033[0m %s\n' "$*"; }
die() { printf '\033[31m[demo] ERROR:\033[0m %s\n' "$*" >&2; exit 2; }

# shellcheck disable=SC2329  # invoked via trap
cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  if [ "${#PIDS[@]}" -gt 0 ]; then
    log "stopping web and worker"
    local p
    for p in "${PIDS[@]}"; do kill -TERM -- "-$p" 2>/dev/null || kill -TERM "$p" 2>/dev/null; done
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      local alive=0
      for p in "${PIDS[@]}"; do kill -0 "$p" 2>/dev/null && alive=1; done
      [ "$alive" = 0 ] && break
      sleep 0.5
    done
    for p in "${PIDS[@]}"; do kill -KILL -- "-$p" 2>/dev/null; done
  fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# ---------------------------------------------------------------- prerequisites
need() { command -v "$1" >/dev/null 2>&1 || die "$1 not found. $2"; }
need docker "Install Docker Desktop (local Supabase runs in containers)."
docker info >/dev/null 2>&1 || die "Docker is installed but not running. Start Docker Desktop and retry."
need node "Install Node 22 (brew install node@22)."
need pnpm "Install pnpm (corepack enable, or brew install pnpm)."
need uv "Install uv (brew install uv); it runs the device software."
need ffmpeg "Install ffmpeg (brew install ffmpeg); the mock camera encodes clips with it."
need ffprobe "Install ffmpeg (brew install ffmpeg); ffprobe verifies the clip."
need curl "Install curl."

cd "$ROOT" || die "cannot cd to $ROOT"
[ -d node_modules ] || { log "pnpm install"; pnpm install >/dev/null || die "pnpm install failed"; }
[ -d device/.venv ] || { log "uv sync"; (cd device && uv sync >/dev/null) || die "uv sync failed"; }

mkdir -p "$LOG_DIR" "$DEMO_DIR"

# ---------------------------------------------------------------- Supabase
if [ "$RESET" = 1 ]; then
  log "supabase db reset (--reset)"
  pnpm exec supabase status >/dev/null 2>&1 || pnpm exec supabase start >"$LOG_DIR/supabase-start.log" 2>&1 \
    || die "supabase start failed, see $LOG_DIR/supabase-start.log"
  pnpm exec supabase db reset >"$LOG_DIR/supabase-reset.log" 2>&1 || die "supabase db reset failed, see $LOG_DIR/supabase-reset.log"
elif ! pnpm exec supabase status >/dev/null 2>&1; then
  log "starting local Supabase (first run pulls images, can take minutes)"
  pnpm exec supabase start >"$LOG_DIR/supabase-start.log" 2>&1 || die "supabase start failed, see $LOG_DIR/supabase-start.log"
fi

# Env for web + worker, straight from the running stack (no keys are hard-coded or committed).
STATUS_ENV="$(pnpm exec supabase status -o env 2>/dev/null)" || die "supabase status failed"
sbval() { printf '%s\n' "$STATUS_ENV" | sed -n "s/^$1=\"\{0,1\}\([^\"]*\)\"\{0,1\}$/\1/p" | head -1; }
SB_URL="$(sbval API_URL)"
SB_ANON="$(sbval ANON_KEY)"
SB_SERVICE="$(sbval SERVICE_ROLE_KEY)"
[ -n "$SB_URL" ] && [ -n "$SB_ANON" ] && [ -n "$SB_SERVICE" ] || die "could not read API_URL/ANON_KEY/SERVICE_ROLE_KEY from 'supabase status -o env'"
curl -fsS -o /dev/null -H "apikey: $SB_SERVICE" -H "Authorization: Bearer $SB_SERVICE" "$SB_URL/rest/v1/devices?select=id&limit=1" \
  || die "Supabase REST at $SB_URL is not answering (is the schema applied? try --reset)"

export NEXT_PUBLIC_SUPABASE_URL="$SB_URL"
export NEXT_PUBLIC_SUPABASE_ANON_KEY="$SB_ANON"
export SUPABASE_SERVICE_ROLE_KEY="$SB_SERVICE"
export SUPABASE_URL="$SB_URL"
export REPORT_MODE="${REPORT_MODE:-fixture}"

# The seed has media rows but no files; attach a clip and a matching sensor trace to the seeded events so the
# backup event (spec section 7) and the history play video. Idempotent. Needs ffmpeg (checked above).
if [ "$RESET" = 1 ]; then
  log "attaching clips and sensor traces to the seeded events (pnpm demo:seed-media)"
  (cd web && pnpm exec tsx scripts/dev-attach-media.ts) >"$LOG_DIR/seed-media.log" 2>&1 \
    || { tail -20 "$LOG_DIR/seed-media.log" >&2; die "seeding media failed, see $LOG_DIR/seed-media.log"; }
fi
export WORKER_POLL_MS="${WORKER_POLL_MS:-1000}"
log "report mode: $REPORT_MODE"

# ---------------------------------------------------------------- web + worker
if [ -z "$PORT" ]; then
  PORT=3400
  while (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; do PORT=$((PORT + 1)); [ "$PORT" -lt 3500 ] || die "no free port in 3400-3499"; done
fi
BASE="http://127.0.0.1:$PORT"

set -m  # each background job gets its own process group, so cleanup can kill the whole tree
if [ "$BUILD" = 1 ] || [ ! -d web/.next ]; then
  log "building web (next build)"
  (cd web && pnpm exec next build) >"$LOG_DIR/web-build.log" 2>&1 || die "web build failed, see $LOG_DIR/web-build.log"
fi
log "starting web on $BASE"
(cd web && exec pnpm exec next start -p "$PORT" -H 127.0.0.1) >"$LOG_DIR/web.log" 2>&1 &
PIDS+=("$!")
log "starting worker"
(cd worker && exec pnpm exec tsx src/main.ts) >"$LOG_DIR/worker.log" 2>&1 &
PIDS+=("$!")

for _ in $(seq 1 60); do
  curl -fsS -o /dev/null "$BASE/login" 2>/dev/null && break
  kill -0 "${PIDS[0]}" 2>/dev/null || die "web exited early, see $LOG_DIR/web.log"
  sleep 1
done
curl -fsS -o /dev/null "$BASE/login" || die "web did not come up on $BASE, see $LOG_DIR/web.log"
sleep 1
grep -q "started:" "$LOG_DIR/worker.log" || { kill -0 "${PIDS[1]}" 2>/dev/null || die "worker exited early, see $LOG_DIR/worker.log"; }

# ---------------------------------------------------------------- scratch device config + pairing
SCRATCH="$DEMO_DIR/config.yaml"
QUEUE="$DEMO_DIR/queue"
rm -rf "$QUEUE"
sed "s|^\(  queue_dir:\).*|\1 $QUEUE|" device/config.yaml >"$SCRATCH"
grep -q "queue_dir: $QUEUE" "$SCRATCH" || die "could not set queue_dir in the scratch config"
CLI=(uv run --project device argus-cli)
log "pairing scratch config as $SERIAL"
"${CLI[@]}" pair --config "$SCRATCH" --url "$BASE" --token "$TOKEN" >"$LOG_DIR/pair.log" 2>&1 \
  || { cat "$LOG_DIR/pair.log" >&2; die "pairing failed"; }
sed 's/^/        /' "$LOG_DIR/pair.log"

# ---------------------------------------------------------------- scenarios
SINCE="$(python3 -c 'from datetime import datetime,timezone,timedelta;print((datetime.now(timezone.utc)-timedelta(seconds=2)).strftime("%Y-%m-%dT%H:%M:%S+00:00"))')"
# scenario : expected event type : seconds to run. Each scenario gets LEAD seconds of quiet lead-in so the
# camera ring holds the full 20 s pre-roll (a real hat has been running for minutes); run time =
# lead-in + time to the event + 10 s post-roll + clip encode margin.
LEAD=21
# The demo runs the fall (with the real ledge-fall video) and the hat removal. The struck-by near miss is opt-in.
SCENARIOS=("hat_drop_1m:fall:40" "hat_removed_table:hat_removed:33")
[ "$STRUCK" = 1 ] && SCENARIOS=("board_swing_rear:struck_by_near_miss:39" "${SCENARIOS[@]}")
EXPECT=()
for spec in "${SCENARIOS[@]}"; do
  IFS=: read -r name etype secs <<<"$spec"
  shifted="$(uv run --project device python scripts/demo_scenario.py "$name" "$LEAD" "$DEMO_DIR/scenarios/$name.yaml")" \
    || die "could not prepare scenario $name"
  log "scenario $name (${secs}s, expects $etype)"
  "${CLI[@]}" run --config "$SCRATCH" --scenario "$shifted" --duration "$secs" --no-control >"$LOG_DIR/device-$name.log" 2>&1 \
    || { tail -20 "$LOG_DIR/device-$name.log" >&2; die "argus-cli run failed for $name"; }
  # The in-process uploader stops with the run; flush whatever is still queued.
  "${CLI[@]}" upload --config "$SCRATCH" --once >"$LOG_DIR/upload-$name.log" 2>&1 \
    || { tail -20 "$LOG_DIR/upload-$name.log" >&2; die "upload failed for $name (see $LOG_DIR/upload-$name.log)"; }
  EXPECT+=("$name:$etype")
done

# ---------------------------------------------------------------- assertions
log "asserting backend state (waits for the worker; target: report ready within 5 min)"
SUMMARY="$DEMO_DIR/summary.json"
uv run --project device python scripts/demo_assert.py --url "$SB_URL" --key "$SB_SERVICE" --serial "$SERIAL" \
  --since "$SINCE" --summary-json "$SUMMARY" "${EXPECT[@]}"
RC=$?

if [ "$RC" = 0 ] && [ "$UI" = 1 ]; then
  log "UI check: opening the fall incident in Playwright"
  EID="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["hat_drop_1m"]["event_id"])' "$DEMO_DIR/summary.json")"
  (cd web && WEB_BASE_URL="$BASE" E2E_INCIDENT_ID="$EID" pnpm exec playwright test -c tests/e2e/playwright.config.ts e2e-struck-by) \
    >"$LOG_DIR/playwright.log" 2>&1 || { tail -30 "$LOG_DIR/playwright.log" >&2; RC=1; }
  [ "$RC" = 0 ] && log "UI check passed (screenshot: web/tests/e2e/__screenshots__/e2e-struck-by.png)"
fi

echo
if [ "$RC" = 0 ]; then printf '\033[32mRESULT: PASS\033[0m\n'; else printf '\033[31mRESULT: FAIL\033[0m (logs: %s)\n' "$LOG_DIR"; fi
if [ "$KEEP" = 1 ]; then
  echo "Dashboard: $BASE   login: demo@argus.local / argus-demo-2026"
else
  echo "Dashboard: stopped with this script. Re-run with --keep-running to browse it (login: demo@argus.local / argus-demo-2026)."
fi
if [ "$KEEP" = 1 ] && [ -f "$SUMMARY" ]; then
  python3 - "$SUMMARY" "$BASE" <<'EOF'
import json, sys
for sc, v in json.load(open(sys.argv[1])).items():
    print(f"  {sc}: {sys.argv[2]}/incidents/{v['event_id']}")
EOF
fi

if [ "$KEEP" = 1 ]; then
  log "--keep-running: web and worker stay up. Ctrl-C to stop."
  while true; do sleep 3600 & wait $!; done
fi
exit "$RC"
