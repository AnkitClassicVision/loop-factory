#!/usr/bin/env python3
"""Executed check for the merge-QA review task.

The reviewer must produce review.md with a first-line VERDICT, evidence
citations (file:line or function names), and explicit findings for each
required audit point. Prints WHY on failure. Exit 0 = PASS.
"""
import re
import sys
from pathlib import Path

fails = []
p = Path("review.md")
if not p.exists():
    print("MERGE-QA CHECK: FAIL\n  - review.md missing")
    sys.exit(1)
t = p.read_text(encoding="utf-8", errors="replace")
first = t.strip().splitlines()[0].strip().upper()
if not re.match(r"^VERDICT:\s*(PASS|FAIL)\b", first):
    fails.append("first line must be 'VERDICT: PASS' or 'VERDICT: FAIL'")
low = t.lower()
for topic, pat in {
    "drift-sensor side audited": r"sense_drift",
    "compare drift findings audited": r"(release_drift|drift_check_failed)",
    "lock/atomic side audited": r"(_act_and_record|records_lock)",
    "escalation dedup audited": r"(fingerprint|dedup)",
    "run_manager_cycle params audited": r"dept_dir",
    "test-suite evidence": r"(check pass|\d+ passed)",
    "conflict-marker sweep": r"(<<<<<<<|conflict marker)",
}.items():
    if not re.search(pat, low):
        fails.append(f"missing {topic} (no /{pat}/ in review)")
if len(re.findall(r"(evidence|line \d|:\d+|def [a-z_]+)", low)) < 5:
    fails.append("fewer than 5 evidence citations — review reads as unverified")
if fails:
    print("MERGE-QA CHECK: FAIL")
    for f in fails:
        print(f"  - {f}")
    sys.exit(1)
print(f"MERGE-QA CHECK: PASS — {first}")
sys.exit(0)
