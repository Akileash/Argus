#!/usr/bin/env bash
# The one command to run before you push or merge.
#
#   scripts/check.sh            everything this machine can run
#   scripts/check.sh --device   only the Python device checks (hardware teammates with no Node installed)
#
# Steps: device pytest, device ruff, pnpm tests, pnpm typecheck, shellcheck, SQL tests.
# A step that cannot run here (tool missing, no Postgres) is reported as SKIP with the reason, never hidden.
# Exit code 0 only when nothing FAILED. SKIPs do not fail the run, but read them: CI runs the same steps.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/opt/postgresql@16/bin:/opt/homebrew/bin:$PATH"

DEVICE_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --device) DEVICE_ONLY=1 ;;
    -h | --help)
      sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "unknown option: $arg (try --help)" >&2
      exit 2
      ;;
  esac
done

LOG="$(mktemp -d)"
trap 'rm -rf "$LOG"' EXIT
RESULTS=()
FAILED=0

# step <name> <command...>: run, show a one-line verdict, print the output tail only on failure.
step() {
  local name="$1"
  shift
  printf '%-22s' "$name"
  if "$@" >"$LOG/out.txt" 2>&1; then
    echo "PASS"
    RESULTS+=("PASS  $name")
  else
    echo "FAIL"
    RESULTS+=("FAIL  $name")
    FAILED=1
    sed 's/^/    | /' "$LOG/out.txt" | tail -n 40
  fi
}

skip() {
  printf '%-22sSKIP (%s)\n' "$1" "$2"
  RESULTS+=("SKIP  $1: $2")
}

have() { command -v "$1" >/dev/null 2>&1; }

# ---- device (Python)
if have uv; then
  step "device pytest" bash -c "cd '$ROOT/device' && uv run pytest -q"
  step "device ruff" bash -c "cd '$ROOT/device' && uv run ruff check argus hardware"
else
  skip "device pytest" "uv not installed: https://docs.astral.sh/uv/"
  skip "device ruff" "uv not installed"
fi

# ---- Node workspaces
if [ "$DEVICE_ONLY" = 1 ]; then
  skip "pnpm test" "--device"
  skip "pnpm typecheck" "--device"
elif ! have pnpm || ! have node; then
  skip "pnpm test" "pnpm/node not installed"
  skip "pnpm typecheck" "pnpm/node not installed"
else
  if [ ! -d "$ROOT/node_modules" ]; then
    step "pnpm install" bash -c "cd '$ROOT' && pnpm install --frozen-lockfile"
  fi
  step "pnpm test" bash -c "cd '$ROOT' && pnpm -r test"
  step "pnpm typecheck" bash -c "cd '$ROOT' && pnpm -r typecheck"
fi

# ---- shell scripts
if have shellcheck; then
  step "shellcheck" bash -c "cd '$ROOT' && git ls-files -z --cached --others --exclude-standard '*.sh' | xargs -0 shellcheck"
else
  skip "shellcheck" "shellcheck not installed (brew install shellcheck)"
fi

# ---- SQL tests (own throwaway Postgres cluster, see supabase/tests/run.sh)
if [ "$DEVICE_ONLY" = 1 ]; then
  skip "sql tests" "--device"
elif have initdb && have pg_ctl && have psql; then
  step "sql tests" bash "$ROOT/supabase/tests/run.sh"
else
  skip "sql tests" "Postgres (initdb, pg_ctl, psql) not installed"
fi

echo
echo "---- summary"
printf '%s\n' "${RESULTS[@]}"
if [ "$FAILED" = 0 ]; then
  echo "OK: nothing failed."
else
  echo "NOT OK: fix the FAIL lines above before you push."
  exit 1
fi
