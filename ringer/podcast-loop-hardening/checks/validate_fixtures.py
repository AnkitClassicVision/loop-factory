#!/usr/bin/env python3
"""Executed check for the poisoned-fixture pack (T5, podcast-loop-hardening r1).

Validates that each required poisoned fixture exists, parses, matches the
frozen watchdog interface contract, and actually encodes the poison it is
named for. Prints WHY on every failure. Exit 0 = PASS.

Run from the task worktree root (CWD = worktree of loop-factory).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

FIXDIR = Path("departments/podcast/tests/fixtures/poisoned")

OBS_KEYS = {"ts", "sensor", "subject", "status", "evidence", "detail"}
INCIDENT_KEYS = {"fingerprint", "failure_class", "first_seen", "last_seen",
                 "state", "severity", "setpoint", "observed", "evidence",
                 "one_question", "count"}

fails: list[str] = []


def fail(msg: str) -> None:
    fails.append(msg)


def load(name: str):
    p = FIXDIR / name
    if not p.exists():
        fail(f"{name}: missing (expected at {p})")
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError as exc:
        fail(f"{name}: not valid JSON: {exc}")
        return None


def parse_ts(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


# 1. unit_missing.json — a sense snapshot whose observation list omits at least
#    one unit named in its own declared inventory (the silent-gap poison).
data = load("unit_missing.json")
if data is not None:
    inventory = data.get("inventory") or []
    observed_subjects = {o.get("subject") for o in data.get("observations", [])}
    if not inventory:
        fail("unit_missing.json: must declare a non-empty 'inventory' list of unit names")
    else:
        missing = [u for u in inventory if u not in observed_subjects]
        if not missing:
            fail("unit_missing.json: every inventory unit appears in observations — the poison (a silently missing unit) is absent")
    for o in data.get("observations", []):
        gaps = OBS_KEYS - set(o)
        if gaps:
            fail(f"unit_missing.json: observation missing contract keys {sorted(gaps)}")

# 2. stale_receipt.json — a loop receipt whose timestamp is older than 26h
#    against the fixture's own 'now'.
data = load("stale_receipt.json")
if data is not None:
    now = parse_ts(data.get("now"))
    ts = parse_ts(data.get("receipt", {}).get("ts"))
    if now is None or ts is None:
        fail("stale_receipt.json: needs parseable ISO8601 'now' and receipt.ts")
    elif now - ts <= timedelta(hours=26):
        fail(f"stale_receipt.json: receipt.ts is only {now - ts} old vs now — must exceed 26h to be the stale poison")

# 3. forged_receipt.json — a receipt whose declared signature/hash does not
#    verify against its own body (any explicit mismatch marker acceptable).
data = load("forged_receipt.json")
if data is not None:
    r = data.get("receipt", {})
    if not r.get("signature"):
        fail("forged_receipt.json: receipt must carry a 'signature' field (the forged one)")
    if data.get("expected_signature") in (None, "",):
        fail("forged_receipt.json: must carry 'expected_signature' (the correct value) so a verifier can prove the mismatch")
    if r.get("signature") == data.get("expected_signature"):
        fail("forged_receipt.json: signature equals expected_signature — no forgery encoded")

# 4. resolved_fingerprint_recurs.json — an incidents map with one incident in
#    state 'resolved' plus a fresh observation reproducing the same fingerprint.
data = load("resolved_fingerprint_recurs.json")
if data is not None:
    incidents = data.get("incidents") or {}
    resolved = [k for k, v in incidents.items() if v.get("state") == "resolved"]
    if not resolved:
        fail("resolved_fingerprint_recurs.json: needs at least one incident with state='resolved'")
    new_obs = data.get("new_observation") or {}
    if new_obs.get("fingerprint") not in resolved:
        fail("resolved_fingerprint_recurs.json: new_observation.fingerprint must match a resolved incident (the recurrence poison)")
    for k, v in incidents.items():
        gaps = INCIDENT_KEYS - set(v) - {"fingerprint"}
        if gaps:
            fail(f"resolved_fingerprint_recurs.json: incident {k} missing contract keys {sorted(gaps)}")

# 5. non_allowlisted_playbook.json — a heal request naming a playbook id that
#    the fixture's own allowlist does not contain.
data = load("non_allowlisted_playbook.json")
if data is not None:
    allow = data.get("playbook_allowlist") or []
    req = (data.get("heal_request") or {}).get("playbook")
    if not allow:
        fail("non_allowlisted_playbook.json: needs a non-empty 'playbook_allowlist'")
    if not req:
        fail("non_allowlisted_playbook.json: heal_request.playbook is required")
    elif req in allow:
        fail("non_allowlisted_playbook.json: heal_request.playbook IS allowlisted — poison absent")

readme = FIXDIR / "README.md"
if not readme.exists() or len(readme.read_text(encoding="utf-8").split()) < 40:
    fail("README.md: missing or under 40 words — must explain each poison and the gate it must fail")

if fails:
    print("POISONED-FIXTURE CHECK: FAIL")
    for f in fails:
        print(" -", f)
    sys.exit(1)
print("POISONED-FIXTURE CHECK: PASS (5 fixtures + README validated)")
