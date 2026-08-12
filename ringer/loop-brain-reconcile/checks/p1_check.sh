#!/usr/bin/env bash
# Verify a P0 worktree task: plan tests green, only owned files changed,
# patch exported outside the worktree (worktrees are deleted on PASS).
# Usage: p0_check.sh <key> "<pytest targets>" <owned-file> [<owned-file>...]
set -uo pipefail
KEY="$1"; TESTS="$2"; shift 2
OWNED=("$@")
PATCH_DIR="/home/ankit114/ringer-work/loop-brain-reconcile-p1/patches"
mkdir -p "$PATCH_DIR"
fail() { echo "CHECK FAIL ($KEY): $*" >&2; exit 1; }

git rev-parse --git-dir >/dev/null 2>&1 || fail "cwd is not a git worktree"

for f in "${OWNED[@]}"; do
  case "$f" in
    *.sh) [ -f "$f" ] && { bash -n "$f" || fail "bash -n failed on $f"; } ;;
  esac
done

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD" python3 -m pytest $TESTS -v \
  || fail "pytest failed for targets: $TESTS (see output above for which assertion broke)"

git add -- "${OWNED[@]}" || fail "git add failed — an owned file is missing (was it created?)"
CHANGED="$(git diff --cached --name-only)"
[ -n "$CHANGED" ] || fail "no staged changes in owned files — nothing was implemented"
while IFS= read -r f; do
  ok=0
  for o in "${OWNED[@]}"; do [ "$f" = "$o" ] && ok=1 && break; done
  [ "$ok" -eq 1 ] || fail "changed file outside ownership: $f"
done <<<"$CHANGED"

git diff --cached --binary > "$PATCH_DIR/$KEY.patch" || fail "patch export failed"
[ -s "$PATCH_DIR/$KEY.patch" ] || fail "exported patch is empty"
echo "CHECK PASS ($KEY): tests green; changed: $(echo "$CHANGED" | tr '\n' ' '); patch: $PATCH_DIR/$KEY.patch"
