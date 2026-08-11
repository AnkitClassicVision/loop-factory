#!/usr/bin/env bash
# T2 check — comms_reconcile_sensor detects the open loop (57 asked → 16 replied → 0 harvested)
# PASS = exit 0. Every failure prints WHY.
set -u
REPO=/mnt/d_drive/repos/loop-factory
fail() { echo "CHECK FAIL: $1"; exit 1; }
cd "$REPO" || fail "cannot cd to repo"

export PYTHONDONTWRITEBYTECODE=1

# 1. Unit tests pass (must include the 57/16/0 detection case and a no-finding control case)
python3 -m pytest tests/test_comms_reconcile_sensor.py -q -p no:cacheprovider \
  || fail "tests/test_comms_reconcile_sensor.py failed (see pytest output above)"

# 2. Fixtures exist
test -f tests/fixtures/comms_reconcile/tracker_57_16_0.json || fail "missing fixture tracker_57_16_0.json"
test -f tests/fixtures/comms_reconcile/referrals_empty.json || fail "missing fixture referrals_empty.json"

# 3. Execute the sensor CLI against the open-loop fixture — must emit an open_loop finding with the right counts
OUT=$(python3 departments/podcast/runtime/comms_reconcile_sensor.py \
  --tracker tests/fixtures/comms_reconcile/tracker_57_16_0.json \
  --ledger  tests/fixtures/comms_reconcile/referrals_empty.json) \
  || fail "sensor CLI crashed on the open-loop fixture"
echo "$OUT" | python3 -c '
import json, sys
d = json.load(sys.stdin)
fs = d.get("findings", [])
ol = [f for f in fs if f.get("code") == "open_loop"]
assert ol, "no open_loop finding emitted for 57->16->0 fixture; findings=" + json.dumps(fs)
f = ol[0]
assert f.get("replied") == 16, "expected replied=16, got %r" % f.get("replied")
assert f.get("harvested") == 0, "expected harvested=0, got %r" % f.get("harvested")
print("open_loop finding OK:", json.dumps(f))
' || fail "sensor output failed content assertions (see message above)"

# 4. Control: healthy fixture must NOT produce an open_loop finding
test -f tests/fixtures/comms_reconcile/tracker_healthy.json || fail "missing fixture tracker_healthy.json"
test -f tests/fixtures/comms_reconcile/referrals_present.json || fail "missing fixture referrals_present.json"
OUT2=$(python3 departments/podcast/runtime/comms_reconcile_sensor.py \
  --tracker tests/fixtures/comms_reconcile/tracker_healthy.json \
  --ledger  tests/fixtures/comms_reconcile/referrals_present.json) \
  || fail "sensor CLI crashed on healthy fixture"
echo "$OUT2" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert not [f for f in d.get("findings", []) if f.get("code") == "open_loop"], \
  "false positive: open_loop emitted on healthy fixture"
print("healthy fixture OK: no open_loop")
' || fail "sensor false-positives on healthy fixture"

# 5. Read-only discipline: sensor must not import send/network machinery
grep -qE "smtplib|requests|urllib|googleapiclient" departments/podcast/runtime/comms_reconcile_sensor.py \
  && fail "sensor imports network/send libraries — it must be a pure local reader"

# 6. No PHI/contact leakage into fixtures (counts only, synthetic ids)
grep -qiE "@(cecofmedina|autographeye|mybcat)\." tests/fixtures/comms_reconcile/*.json \
  && fail "fixture contains a real contact domain — fixtures must be synthetic"

# 7. Scope (cumulative: T1's files allowed to remain modified)
ALLOWED='^(factory/graphs\.py|tests/test_comms_invariant_lint\.py|departments/podcast/runtime/comms_reconcile_sensor\.py|tests/test_comms_reconcile_sensor\.py|tests/fixtures/comms_reconcile/)'
GUARDED='^(factory/|departments/|kernel/|interview/|templates/|runbooks/|loopfactory\.py|tests/)'
VIOL=$( { git diff --name-only; git diff --cached --name-only; git ls-files --others --exclude-standard; } | sort -u \
  | grep -E "$GUARDED" | grep -vE "$ALLOWED" )
[ -n "$VIOL" ] && fail "files changed outside T1+T2 ownership: $VIOL"

# 8. Governance untouched
git diff --name-only | grep -qx "departments/podcast/charter.yaml" && fail "GOVERNANCE VIOLATION: charter.yaml modified"

echo "T2 PASS: sensor detects 57/16/0 open loop, no false positive, read-only, fixtures synthetic, scope clean"
exit 0
