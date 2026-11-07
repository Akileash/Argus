#!/usr/bin/env bash
# Runs every browser test against a running stack, with the Supabase settings the specs need.
# Start the stack first: scripts/demo-e2e.sh --reset --keep-running
# Usage: scripts/web-e2e.sh [--url http://127.0.0.1:3400] [playwright args...]
set -euo pipefail
cd "$(dirname "$0")/.."
URL="http://127.0.0.1:3400"
if [ "${1:-}" = "--url" ]; then URL="$2"; shift 2; fi
env_out="$(pnpm exec supabase status -o env 2>/dev/null)" || { echo "Supabase is not running. Start the stack first." >&2; exit 1; }
value() { printf '%s\n' "$env_out" | sed -n "s/^$1=\"\{0,1\}\([^\"]*\)\"\{0,1\}$/\1/p"; }
NEXT_PUBLIC_SUPABASE_URL="$(value API_URL)"
NEXT_PUBLIC_SUPABASE_ANON_KEY="$(value ANON_KEY)"
SUPABASE_SERVICE_ROLE_KEY="$(value SERVICE_ROLE_KEY)"
export NEXT_PUBLIC_SUPABASE_URL NEXT_PUBLIC_SUPABASE_ANON_KEY SUPABASE_SERVICE_ROLE_KEY
export WEB_BASE_URL="$URL"
cd web
exec pnpm exec playwright test -c tests/e2e/playwright.config.ts "$@"
