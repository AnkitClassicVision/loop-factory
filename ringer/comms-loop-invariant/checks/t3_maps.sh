#!/usr/bin/env bash
# T3 check — procedural map + subgraphs + interview question + charter PROPOSAL (draft only)
# PASS = exit 0. Every failure prints WHY.
set -u
REPO=/mnt/d_drive/repos/loop-factory
fail() { echo "CHECK FAIL: $1"; exit 1; }
cd "$REPO" || fail "cannot cd to repo"

export PYTHONDONTWRITEBYTECODE=1

# 1. Governance file MUST be untouched — checked first, loudest
git diff --name-only | grep -qx "departments/podcast/charter.yaml" \
  && fail "GOVERNANCE VIOLATION: charter.yaml was modified — charter changes are human-only; this task produces a proposal draft"

# 2. The full validate (lint + traceability + guard-matrix) passes with the new node
python3 loopfactory.py validate --name podcast \
  || fail "loopfactory validate --name podcast failed — map patch is inconsistent (its output above says which node/trace broke)"

# 3. Sensor node present in BOTH map forms
grep -qi "comms_reconcile" departments/podcast/subgraphs.json \
  || fail "subgraphs.json has no comms_reconcile node"
grep -qi "comms" departments/podcast/procedural-graph.md \
  || fail "procedural-graph.md does not mention the comms reconcile step"

# 4. Interview bank carries the return-path question for FUTURE departments
grep -qiE "return[ _-]path" interview/QUESTION_BANK.md \
  || fail "QUESTION_BANK.md lacks a return-path question"
grep -qiE "sla" interview/QUESTION_BANK.md \
  || fail "QUESTION_BANK.md return-path question lacks an SLA component"

# 5. Charter proposal draft exists, is labeled as human-gated, and carries the invariant
P=ringer/comms-loop-invariant/charter-proposal.md
test -f "$P" || fail "missing $P"
grep -qi "comms_loop_invariant" "$P" || fail "$P lacks the comms_loop_invariant block"
grep -qiE "return_path" "$P" || fail "$P lacks return_path requirement"
grep -qiE "return_sla_hours" "$P" || fail "$P lacks return_sla_hours requirement"
grep -qiE "human sign|owner sign|requires ankit|human-only" "$P" \
  || fail "$P is not clearly marked as requiring human sign-off"

# 6. Scope (cumulative allowed set for T1+T2+T3)
ALLOWED='^(factory/graphs\.py|tests/test_comms_invariant_lint\.py|departments/podcast/runtime/comms_reconcile_sensor\.py|tests/test_comms_reconcile_sensor\.py|tests/fixtures/comms_reconcile/|departments/podcast/procedural-graph\.md|departments/podcast/subgraphs\.json|interview/QUESTION_BANK\.md|ringer/comms-loop-invariant/)'
GUARDED='^(factory/|departments/|kernel/|interview/|templates/|runbooks/|loopfactory\.py|tests/)'
VIOL=$( { git diff --name-only; git diff --cached --name-only; git ls-files --others --exclude-standard; } | sort -u \
  | grep -E "$GUARDED" | grep -vE "$ALLOWED" )
[ -n "$VIOL" ] && fail "files changed outside cumulative T1+T2+T3 ownership: $VIOL"

echo "T3 PASS: validate clean, node in both maps, interview question added, charter proposal drafted (human-gated), governance untouched, scope clean"
exit 0
