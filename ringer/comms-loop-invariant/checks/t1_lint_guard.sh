#!/usr/bin/env bash
# T1 check — comms-invariant lint guard in factory/graphs.py
# PASS = exit 0. Every failure prints WHY.
set -u
REPO=/mnt/d_drive/repos/loop-factory
fail() { echo "CHECK FAIL: $1"; exit 1; }
cd "$REPO" || fail "cannot cd to repo"

export PYTHONDONTWRITEBYTECODE=1

# 1. New unit tests pass (they contain the RED case: emits_ask without return_path must error)
python3 -m pytest tests/test_comms_invariant_lint.py -q -p no:cacheprovider \
  || fail "tests/test_comms_invariant_lint.py failed — lint guard logic wrong or test missing (see pytest output above)"

# 2. Guard actually lives in factory/graphs.py
grep -qn "emits_ask" factory/graphs.py || fail "factory/graphs.py has no emits_ask handling"
grep -qn "return_path" factory/graphs.py || fail "factory/graphs.py has no return_path requirement"
grep -qn "return_sla_hours" factory/graphs.py || fail "factory/graphs.py has no return_sla_hours requirement"

# 3. Backward compatible: BOTH existing departments still validate clean
python3 loopfactory.py validate --name podcast || fail "loopfactory validate --name podcast broke (guard not backward-compatible; its output above says why)"
python3 loopfactory.py validate --name social  || fail "loopfactory validate --name social broke (guard not backward-compatible; its output above says why)"

# 4. Full factory test suite still green
python3 -m pytest tests/ -q -p no:cacheprovider || fail "existing factory test suite regressed (see pytest output above)"

# 5. Scope: nothing changed on guarded surfaces outside the owned set
ALLOWED='^(factory/graphs\.py|tests/test_comms_invariant_lint\.py)$'
GUARDED='^(factory/|departments/|kernel/|interview/|templates/|runbooks/|loopfactory\.py|tests/)'
VIOL=$( { git diff --name-only; git diff --cached --name-only; git ls-files --others --exclude-standard; } | sort -u \
  | grep -E "$GUARDED" | grep -vE "$ALLOWED" )
[ -n "$VIOL" ] && fail "files changed outside T1 ownership: $VIOL"

# 6. Governance untouched
git diff --name-only | grep -qx "departments/podcast/charter.yaml" && fail "GOVERNANCE VIOLATION: charter.yaml modified"

echo "T1 PASS: lint guard present, RED test green, podcast+social validate clean, suite green, scope clean"
exit 0
