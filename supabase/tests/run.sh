#!/usr/bin/env bash
# Applies stubs + migrations + seed to a throwaway Postgres cluster, runs
# supabase/tests/*.test.sql, then validates seeded report JSON against the contract.
# Needs: postgresql@16 (brew) and uv (for the validator). Fully hermetic: own initdb, own socket.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; SB="$(dirname "$HERE")"; ROOT="$(dirname "$SB")"
PGBIN="${PGBIN:-/opt/homebrew/opt/postgresql@16/bin}"
[ -d "$PGBIN" ] || PGBIN="$(dirname "$(command -v pg_ctl)")"
export PATH="$PGBIN:/opt/homebrew/bin:$PATH"
TMP="$(mktemp -d)"; PORT="${PGPORT_TEST:-54329}"
cleanup() { pg_ctl -D "$TMP/data" -m immediate stop >/dev/null 2>&1; rm -rf "$TMP"; }
trap cleanup EXIT
initdb -D "$TMP/data" -U postgres -A trust >/dev/null || exit 1
pg_ctl -D "$TMP/data" -o "-p $PORT -k $TMP -c listen_addresses=''" -l "$TMP/log" -w start >/dev/null || exit 1
PSQL=(psql -h "$TMP" -p "$PORT" -U postgres -X -q -v ON_ERROR_STOP=1)
"${PSQL[@]}" -d postgres -c "create database argus_test" || exit 1
PSQL+=(-d argus_test); TQ=(-tA)
"${PSQL[@]}" -f "$HERE/stubs.sql" || exit 1
for f in "$SB"/migrations/*.sql; do echo "migrate $(basename "$f")"; "${PSQL[@]}" -f "$f" || exit 1; done
"${PSQL[@]}" -f "$SB/seed.sql" || exit 1
fail=0
for t in "$HERE"/*.test.sql; do
  echo "== $(basename "$t")"
  if "${PSQL[@]}" "${TQ[@]}" -f "$t" 2>&1 | sed 's/^/   /'; [ "${PIPESTATUS[0]}" = 0 ]; then echo "   ok"; else echo "   FAILED"; fail=1; fi
done
echo "== validate_seed_reports.py"
export PSQL_CMD="psql -h $TMP -p $PORT -U postgres -d argus_test -X -At"
( cd "$ROOT/device" && uv run python ../supabase/tests/validate_seed_reports.py ) || fail=1
if [ $fail = 0 ]; then echo "ALL PASSED"; else echo "SOME FAILED"; exit 1; fi
