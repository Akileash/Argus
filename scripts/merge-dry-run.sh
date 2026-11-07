#!/usr/bin/env bash
# Proof that two hardware teammates can work in parallel and merge with no conflicts. Everything happens in a
# throwaway clone of THIS repo's committed HEAD (uncommitted changes are not included); your branches are untouched.
#
#   teammate A: IMU script + mapping, on its own branch, with a private config.local.yaml
#   teammate B: range script + mapping, on its own branch, with a private config.local.yaml
#   integrator: merges both into a third branch: no conflicts, `scripts/check.sh --device` green,
#               and `argus-cli run` really runs both modules on team code
#   then two people change the lockfiles: the conflict is resolved with scripts/fix-lockfiles.sh
#
# Usage: scripts/merge-dry-run.sh [--keep]     (--keep leaves the temp clone for inspection)
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:$PATH"
KEEP=0
[ "${1:-}" = "--keep" ] && KEEP=1

TMP="$(mktemp -d)"
if [ "$KEEP" = 1 ]; then echo "keeping $TMP"; else trap 'rm -rf "$TMP"' EXIT; fi
step() { printf '\n== %s\n' "$*"; }
die() {
  echo "DRY RUN FAILED: $*" >&2
  exit 1
}
ok() { echo "   ok: $*"; }

if [ -n "$(git -C "$ROOT" status --porcelain)" ]; then
  echo "note: you have uncommitted changes; the dry run uses the last commit only."
fi

step "clone the repo (committed HEAD) into $TMP/hq"
git clone -q --no-hardlinks "$ROOT" "$TMP/hq" || die "clone"
cd "$TMP/hq" || die "cd"
git config user.name "dry run"
git config user.email "dry-run@example.invalid"
git checkout -q -b dry-base
BASE="$(git rev-parse HEAD)"

# --------------------------------------------------------------------------------------------------------
step "teammate A (imu): own worktree, own branch"
git worktree add -q -b teammate/imu "$TMP/a" "$BASE" || die "worktree a"
A="$TMP/a/device/hardware/team/imu"
cp "$TMP/a/device/tests/team/fakes/fake_imu.py" "$A/vendor/imu_test.py" # their script, copied unchanged
cat >"$A/mapping.yaml" <<'EOF'
command: [python, vendor/imu_test.py]
format: json
fields: { ax: accel.x, ay: accel.y, az: accel.z, gx: gyro_rads.0, gy: gyro_rads.1, gz: gyro_rads.2 }
accel_unit: m/s2
gyro_unit: rad/s
axes: { x: y, y: -x, z: z }
EOF
cat >"$TMP/a/device/config.local.yaml" <<'EOF'
modules:
  imu: { impl: team, fallback: mock }
EOF
cp "$TMP/a/device/tests/team/samples/imu.txt" "$TMP/imu-sample.txt"
(cd "$TMP/a/device" && uv run argus-cli team check imu --sample "$TMP/imu-sample.txt" | tail -n 3) || die "A: team check"
git -C "$TMP/a" check-ignore -q device/config.local.yaml || die "config.local.yaml must be gitignored"
git -C "$TMP/a" add -A
CHANGED="$(git -C "$TMP/a" diff --cached --name-only)"
# shellcheck disable=SC2001
echo "$CHANGED" | sed 's/^/   commits: /'
echo "$CHANGED" | grep -qv '^device/hardware/team/imu/' && die "A touched files outside device/hardware/team/imu/"
git -C "$TMP/a" commit -q -m "feat(imu): team imu script and mapping" || die "A commit"
ok "A changed only device/hardware/team/imu/; config.local.yaml stayed local"

# --------------------------------------------------------------------------------------------------------
step "teammate B (range): own worktree, own branch"
git worktree add -q -b teammate/range "$TMP/b" "$BASE" || die "worktree b"
B="$TMP/b/device/hardware/team/range"
cp "$TMP/b/device/tests/team/fakes/fake_range.py" "$B/vendor/range_text.py"
cat >"$B/mapping.yaml" <<'EOF'
command: [python, vendor/range_text.py, --style, text]
format: text
unit: cm
no_echo: [0, -1]
sensors: { R2: R2, R3: R3 }
EOF
cat >"$TMP/b/device/config.local.yaml" <<'EOF'
modules:
  range: { impl: team, fallback: mock }
EOF
cp "$TMP/b/device/tests/team/samples/range_text.txt" "$TMP/range-sample.txt"
(cd "$TMP/b/device" && uv run argus-cli team check range --sample "$TMP/range-sample.txt" | tail -n 3) || die "B: team check"
git -C "$TMP/b" add -A
CHANGED="$(git -C "$TMP/b" diff --cached --name-only)"
# shellcheck disable=SC2001
echo "$CHANGED" | sed 's/^/   commits: /'
echo "$CHANGED" | grep -qv '^device/hardware/team/range/' && die "B touched files outside device/hardware/team/range/"
git -C "$TMP/b" commit -q -m "feat(range): team range script and mapping" || die "B commit"
ok "B changed only device/hardware/team/range/; config.local.yaml stayed local"

# --------------------------------------------------------------------------------------------------------
step "integrator: merge both into a third branch"
git checkout -q -b integration "$BASE" || die "branch"
git merge --no-edit teammate/imu >/dev/null 2>&1 || die "merging teammate/imu conflicted"
git merge --no-edit teammate/range >/dev/null 2>&1 || die "merging teammate/range conflicted"
[ -z "$(git diff --name-only --diff-filter=U)" ] || die "unmerged files"
ok "both merges clean, no conflicts"
git log --oneline -n 4 | sed 's/^/   /'

cat >device/config.local.yaml <<'EOF'
modules:
  imu:   { impl: team, fallback: mock }
  range: { impl: team, fallback: mock }
EOF
step "argus-cli run for 4 s with both modules on team code (config.local.yaml)"
RUN_OUT="$(cd device && uv run argus-cli run --duration 4 --no-control 2>&1)" || {
  echo "$RUN_OUT" | tail -n 20
  die "argus-cli run failed"
}
echo "$RUN_OUT" | grep "buffered" | tail -n 1 | sed 's/^/   /'
echo "$RUN_OUT" | grep "buffered" | tail -n 1 | grep -q "'imu': 'team'" || die "imu is not running on the team impl"
echo "$RUN_OUT" | grep "buffered" | tail -n 1 | grep -q "'range': 'team'" || die "range is not running on the team impl"
echo "$RUN_OUT" | grep -q "module_fallback" && die "a module fell back"
ok "imu and range ran on team code, no fallback"
rm device/config.local.yaml

step "scripts/check.sh --device on the merged branch"
scripts/check.sh --device || die "check.sh --device is not green on the merged branch"

# --------------------------------------------------------------------------------------------------------
step "lockfile conflict: two people both change pnpm-lock.yaml and device/uv.lock"
git checkout -q -b lock-a integration
echo "# regenerated by person A" >>pnpm-lock.yaml
echo "# regenerated by person A" >>device/uv.lock
git commit -q -am "chore: lockfiles from A" || die "lock-a commit"
git checkout -q -b lock-b integration
echo "# regenerated by person B" >>pnpm-lock.yaml
echo "# regenerated by person B" >>device/uv.lock
git commit -q -am "chore: lockfiles from B" || die "lock-b commit"
git checkout -q lock-a
git merge --no-edit lock-b >/dev/null 2>&1 && die "expected a lockfile conflict, got a clean merge"
git diff --name-only --diff-filter=U | sed 's/^/   conflict: /'
git diff --name-only --diff-filter=U | grep -qx "pnpm-lock.yaml" || die "pnpm-lock.yaml should conflict"
git diff --name-only --diff-filter=U | grep -qx "device/uv.lock" || die "device/uv.lock should conflict"
echo "   running scripts/fix-lockfiles.sh (the documented rule: take one side, regenerate)"
LOCK_OFFLINE=1 scripts/fix-lockfiles.sh || die "fix-lockfiles.sh failed"
[ -z "$(git diff --name-only --diff-filter=U)" ] || die "files still unmerged"
! grep -q '^<<<<<<<' pnpm-lock.yaml device/uv.lock || die "conflict markers left in a lockfile"
git commit -q --no-edit || die "merge commit"
ok "lockfile conflict resolved with scripts/fix-lockfiles.sh and committed"

echo
echo "DRY RUN PASSED: parallel teammate branches merged cleanly, check.sh --device green, lockfile conflict resolved."
