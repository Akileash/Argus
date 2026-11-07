#!/usr/bin/env bash
# Resolve a merge or rebase conflict in the generated lockfiles (pnpm-lock.yaml, device/uv.lock).
#
# The rule: a lockfile is generated, so never merge it by hand. Take either side, then regenerate it from
# the merged package.json / pyproject.toml, so it contains everybody's dependencies.
#
# Usage, in the middle of a conflicted merge or rebase:
#   scripts/fix-lockfiles.sh
# Set LOCK_OFFLINE=1 to regenerate from the local package caches only (no network).
# Other conflicted files are left alone and listed: fix those by hand, then `git add` them.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1
OFFLINE="${LOCK_OFFLINE:+1}"

conflicted="$(git diff --name-only --diff-filter=U)"
if [ -z "$conflicted" ]; then
  echo "no conflicted files: nothing to do"
  exit 0
fi

status=0
others=()
while IFS= read -r f; do
  case "$f" in
    pnpm-lock.yaml)
      git checkout --ours -- "$f" || exit 1
      echo "regenerating $f ..."
      if pnpm install --lockfile-only ${OFFLINE:+--offline}; then
        git add "$f"
      else
        echo "FAILED to regenerate $f: run 'pnpm install --lockfile-only' once you are online, then 'git add $f'" >&2
        status=1
      fi
      ;;
    device/uv.lock)
      git checkout --ours -- "$f" || exit 1
      echo "regenerating $f ..."
      if (cd device && uv lock ${OFFLINE:+--offline}); then
        git add "$f"
      else
        echo "FAILED to regenerate $f: run 'cd device && uv lock' once you are online, then 'git add $f'" >&2
        status=1
      fi
      ;;
    *) others+=("$f") ;;
  esac
done <<<"$conflicted"

if [ "${#others[@]}" -gt 0 ]; then
  echo "still conflicted (resolve by hand, then git add):"
  printf '  %s\n' "${others[@]}"
  status=1
fi
[ "$status" = 0 ] && echo "lockfiles resolved and staged. Continue with: git commit  (or git rebase --continue)"
exit "$status"
