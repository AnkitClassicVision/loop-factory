#!/usr/bin/env python3
"""Build manifest-r4-fixes.json — fix swarm for the 16 accepted review findings
(13 from the Codex sense/learn/tests review, 3 from the Claude spine/content
reviews). Review and fix are separate actors: builders here never re-judge the
findings, they implement them. Disjoint ownership mirrors r1 lanes.
Rejected finding (documented): 'proposal generation lacks cross-model QA' —
proposals terminate at the human approval queue by charter design (C10)."""
import json
from pathlib import Path

REPO = "/mnt/d_drive/repos/loop-factory"
WORKDIR = "/home/ankit114/ringer-work/social-dept-build-r4"
CHECK = "/home/ankit114/repos/ringer/templates/fix-swarm/checks/fix-swarm.py"

COMMON = """HARD RULES: Your current working directory IS a git worktree of /mnt/d_drive/repos/loop-factory at commit 7d79b23 — edit files here directly. Do NOT git commit/branch/push; leave changes uncommitted. Do not load skills or call MCP/Apps. No network; tests use fakes only. No secrets/PHI anywhere. Own ONLY your listed files. Python stdlib + PyYAML only; logging, not print.
CONTEXT: departments/social is a governed shadow-mode department. Read its charter.yaml, procedural-graph.md, and concept-map.md before editing — every fix below enforces a charter law (fail-closed, never-fabricate, charter-values-never-hardcoded, receipts-before-advancement, caps count ALL authors). Fixes must not weaken any existing passing test's contract; extend tests, don't delete assertions.
OUTPUT CONTRACT: all listed findings fixed, tests green under HOW TO RUN, plus ./fix-summary.md ('# Fix Summary', '## Summary', '## Files Changed', '## Verification', '## Assumptions', <700 words)."""


def pytest_cmd(*paths):
    return "PYTHONDONTWRITEBYTECODE=1 python3 -m pytest " + " ".join(paths) + " -q -p no:cacheprovider"


def check_cmd(key, verify, owned):
    return (
        f"python3 '{CHECK}' --verify-command '{verify}' "
        f"--patch '{WORKDIR}/{key}.patch' --summary fix-summary.md "
        f"--exported-summary '{WORKDIR}/{key}.summary.md' "
        f"--owned-files '{','.join(owned)}'"
    )


DEPT_TESTS = "departments/social/tests/"

TASKS = []

owned = [
    "departments/social/runtime/pull_zernio_analytics.py",
    "departments/social/runtime/compare_charter.py",
    "departments/social/runtime/assemble_weekly_digest.py",
    "departments/social/tests/test_sense_lane.py",
]
verify = pytest_cmd(DEPT_TESTS)
TASKS.append({
    "key": "fix-sense",
    "engine": "codex",
    "task_type": "code-fix",
    "timeout_s": 3600,
    "spec": f"""You are a fix worker on the social department's SG-SENSE lane. Implement EXACTLY these confirmed review findings.

{COMMON}

OWNERSHIP: {', '.join(owned)}.

FINDINGS TO FIX:
1. [P1] pull_zernio_analytics.py:107 — a post lacking `platform_verified` is emitted as platform_verified=1.0 (fabricated success). Require an explicit boolean; posts with missing/ambiguous verification are quarantined-from-metrics (emit a status:missing row for that post), and if NO usable feed evidence remains overall, exit 3 with status:missing. Never invent a verified row.
2. [P1] compare_charter.py:116 — an upstream {{"status":"missing","source":"zernio"}} marker row is currently absorbed as posts_count=0 and produces cap/faux-work verdicts from invented zeros. Validate observation schema; ANY required-source missing marker → write status:missing to --out and exit 3. No verdicts from missing data.
3. [P1] assemble_weekly_digest.py:242 — a missing/invalid charter still yields a successful digest. On CharterError (factory/charter_loader.py): write status:missing and exit 3 (fail closed).
4. [P1] assemble_weekly_digest.py:170 — a malicious/malformed `metric` field is rendered verbatim into the digest (content-leak channel). Allowlist known metric identifiers; render only normalized labels and numeric values; unknown metric names are counted ('N unrecognized rows') but never rendered raw.
5. [P1] test_sense_lane.py:222 — the fixture omitting platform_verified currently expects success, masking finding 1. Rewrite that case: assert the missing-verification post produces a missing row and no verified metric; add the outage-marker negative case for compare_charter (finding 2) and the CharterError case for the digest (finding 3), and a raw-metric-name injection case for finding 4.
6. [P2] test_sense_lane.py:307 — threshold-mutation coverage only flips weekly ceiling + faux-work floor. Independently mutate EVERY charter threshold consumed by compare_charter (delivery target, budget_near_frac, budget ceilings, pace_ceiling_near_frac) and assert each flips its signal.

HOW TO RUN (this is also the check): {verify}""",
    "check": check_cmd("fix-sense", verify, owned),
    "expect_files": [],
    "verified": "sense lane can no longer fabricate verification, absorb outage markers as zeros, digest without a charter, or render raw metric names; every charter threshold is mutation-tested",
})

owned = [
    "departments/social/runtime/read_metrics_records.py",
    "departments/social/runtime/propose_insights.py",
    "departments/social/runtime/proposal_card_to_outbox.py",
    "departments/social/tests/test_learn_lane.py",
]
verify = pytest_cmd(DEPT_TESTS)
TASKS.append({
    "key": "fix-learn",
    "engine": "codex",
    "task_type": "code-fix",
    "timeout_s": 3600,
    "spec": f"""You are a fix worker on the social department's SG-LEARN lane. Implement EXACTLY these confirmed review findings.

{COMMON}

OWNERSHIP: {', '.join(owned)}.

FINDINGS TO FIX:
1. [P1] read_metrics_records.py:93 — any row with minimal keys is copied wholesale into the evidence pack (self-reported claims, PHI-shaped fields, or raw bodies could reach the model). Enforce: only rows whose `source` is an SG-SENSE producer (allowlist: zernio, calendar_join, compare_charter), field-whitelist each row (metric/value/status/source/ts/post_ref/surface/row_id only — drop everything else), reject rows carrying obviously sensitive keys (body, message, text, email, phone), and mark the pack {{"sanitized": true}} with per-aggregate provenance preserved.
2. [P1] proposal_card_to_outbox.py:39 — direct CLI input with any non-empty evidence string bypasses the pack-membership validation done in propose_insights. Require --evidence-pack and re-verify EVERY evidence id against it at append time; unverifiable card → rejected (exit 2), never queued.
3. [P1] proposal_card_to_outbox.py:122 — the --out receipt path is unrestricted and could overwrite governance files. Constrain all writes to inside <state-dir>: resolve the path and refuse (exit 2) anything that escapes state/ or targets charter/runbook/graph files by name.
4. [P1] proposal_card_to_outbox.py:67 — read-check-append dedup races under concurrency. Hold an flock on a lock file in state/ across the whole read-check-append (mirror the records_lock pattern in departments/social/runtime/record.py — read it for the pattern; do not modify it).
5. [P1] propose_insights.py:16 — engine allowlist hardcoded. Load `budget.engine_allowlist` from the charter (factory/charter_loader.py load; plain dict access — do NOT modify charter_loader.py, another lane owns it). Missing/empty allowlist in charter → refuse to run (fail closed), never fall back to a builtin list.
6. [P2] proposal_card_to_outbox.py:89 — TTL hardcoded at 24. Read `escalation.no_reply_ttl_hours` from the charter; test mutates the charter value and asserts the card follows.
7. Tests (test_learn_lane.py): add negative cases for every fix above — self-reported/sensitive rows dropped, direct-outbox bypass rejected, out-path escape rejected, concurrent dedup (two processes or two sequential appends with a torn state simulation), charter-driven allowlist and TTL mutations.

HOW TO RUN (this is also the check): {verify}""",
    "check": check_cmd("fix-learn", verify, owned),
    "expect_files": [],
    "verified": "learn lane accepts only whitelisted independent sense rows, outbox re-verifies evidence and cannot write outside state/, dedup holds a lock, allowlist and TTL come from the charter",
})

owned = [
    "departments/social/runtime/assemble_context.py",
    "departments/social/runtime/draft_post.py",
    "departments/social/runtime/qa_post.py",
    "factory/charter_loader.py",
    "departments/social/tests/test_llm_nodes.py",
    "departments/social/tests/test_republish_scripts.py",
]
verify = pytest_cmd(DEPT_TESTS, "tests/")
TASKS.append({
    "key": "fix-content",
    "engine": "codex",
    "task_type": "code-fix",
    "timeout_s": 3600,
    "engine_args": ["-c", "model_reasoning_effort=high"],
    "spec": f"""You are a fix worker on the social department's content pipeline plus one FACTORY-layer accessor addition. Implement EXACTLY these confirmed review findings.

{COMMON}

OWNERSHIP: {', '.join(owned)}. NOTE factory/charter_loader.py is FACTORY code: department-agnostic additions only — generic accessors, no 'social' anywhere in it. Existing factory tests (tests/) must stay green.

FINDINGS TO FIX:
1. [P1] assemble_context.py:70-77 — the _placeholder() TODO_/empty check runs on brand/offer fields but never on body_text (or item.title/item.url). A body file containing 'TODO_TRANSCRIBE_EPISODE' passes complete:true and reaches drafting, breaching the never_draft_from_fragments floor. Apply _placeholder() to body_text, item.title, item.url; placeholder hit → complete:false + named entry in missing[] + exit 2.
2. [P2] draft_post.py:22,423 + qa_post.py:22 — engine allowlist and max edit rounds are hardcoded literals duplicated across files while charter.yaml owns them (budget.engine_allowlist, qa_shape.max_edit_rounds). Add generic accessors to factory/charter_loader.py (e.g. engine_allowlist(charter), max_edit_rounds(charter), following the existing accessor style there; fail closed on missing keys — no defaults) and use them in draft_post.py and qa_post.py. Do not leave any duplicate literal behind.
3. [P2] draft_post.py:344-350, qa_post.py:322-330 — the grounded-sources deterministic check passes an EMPTY sources[] whenever the body has no numeric tokens. Add: sources[] must contain >=1 entry for every draft (all drafts derive from a source item — an empty sources list is always a defect 'missing_sources'), keeping the existing numeric-token coverage check on top. Update test_deterministic_checks_stay_silent_on_clean_draft's clean fixture to carry a source entry.
4. Tests: test_republish_scripts.py — placeholder-body case (TODO_ stub body → exit 2, complete:false, missing names body_text). test_llm_nodes.py — charter-mutation tests: change engine_allowlist / max_edit_rounds in a temp charter fixture and assert behavior follows; empty-sources defect fires; missing charter keys → refuse to run (fail closed).

HOW TO RUN (this is also the check): {verify}""",
    "check": check_cmd("fix-content", verify, owned),
    "expect_files": [],
    "verified": "placeholder stubs can no longer reach drafting, allowlist and edit rounds are charter-read via new generic factory accessors, empty sources[] is always a defect; factory tests stay green",
})

owned = [
    "departments/social/runtime/social_daily.sh",
    "departments/social/runtime/dispatch.py",
    "departments/social/runtime/kernel_bridge.py",
    "departments/social/tests/test_publish_chain.py",
    "departments/social/tests/test_guards.py",
]
verify = pytest_cmd(DEPT_TESTS)
TASKS.append({
    "key": "fix-spine",
    "engine": "codex",
    "task_type": "code-fix",
    "timeout_s": 5400,
    "engine_args": ["-c", "model_reasoning_effort=high"],
    "spec": f"""You are a fix worker on the social department's publish spine. Implement EXACTLY these confirmed review findings. Everything stays fail-closed; do not weaken any existing guard or test.

{COMMON}

OWNERSHIP: {', '.join(owned)}. guards.py and record.py are READ-ONLY to you (invoke them, don't edit them).

FINDINGS TO FIX:
1. [P1] social_daily.sh:102-110 + dispatch.py:157-161 — S6 kill and S7 breaker are evaluated once at top-of-run; the procedural graph requires them continuous across the lane. A condition landing in observations.jsonl during the (slow) draft/QA rounds does not block that same run's dispatch: _check_stop_markers only tests pre-existing marker FILES and nothing re-runs guards.py kill/breaker before N6. Fix: in social_daily.sh, re-run `guards.py breaker` (surface-scoped) AND `guards.py kill` against current observations immediately before the dispatch step (with receipts); additionally make dispatch.py refuse when the markers appear between token mint and send (re-check markers as the last action before the gateway call). Tests: a mid-run-tripped condition (marker/observation written after selection but before dispatch) blocks THAT run's dispatch.
2. [P3] kernel_bridge.py:63-71 — require_shadow(live=True) raises only on state=='shadow', silently passing 'draft_only'; today safe only via dispatch.py call order. Define one shared NON_LIVE_STATES constant (shadow, draft_only) in kernel_bridge.py, use it in require_shadow AND import/use it in dispatch.py's own check so the guard is correct standalone. Test: require_shadow(live=True) raises in draft_only state too.
3. [P1] test_publish_chain.py:54 — two non-negotiable laws are untested: (a) the all-author per-surface cap (charter cap_scope: all_authors_via_zernio_count) — tests mint tokens directly and never present a surface already filled by OTHER authors; (b) receipt-gated advancement — no test drives social_daily.sh past a missing/invalid receipt. Fix: dispatch.py (or its kernel frequency path) must accept a --surface-counts <json> input (per-surface post counts from the SG-SENSE zernio pull, i.e. ALL authors); count >= charter per-surface cap → YIELD (write a receipt with status 'yielded', exit 0, no dispatch). Read the caps from the charter (setpoints.operational.per_surface_daily_cap, x_daily_cap) — never hardcode. Tests: surface at cap via other authors → yield receipt + zero dispatch attempts (fake zernio NOT invoked); missing --surface-counts in shadow → refuse (fail closed, exit 2); social_daily.sh driver test: truncate/corrupt one step's receipt and assert the chain stops with an incident_candidates entry and no later-step receipts exist.

HOW TO RUN (this is also the check): {verify}""",
    "check": check_cmd("fix-spine", verify, owned),
    "expect_files": [],
    "verified": "kill/breaker now re-evaluated at dispatch time (mid-run trip blocks same-run dispatch), non-live states shared constant, all-author cap yields from charter values, missing-receipt chain stop proven by a driver test",
})

manifest = {
    "run_name": "social-dept-build",
    "workdir": WORKDIR,
    "max_parallel": 4,
    "worktrees": True,
    "repo": REPO,
    "tasks": TASKS,
}

out = Path(__file__).parent / "manifest-r4-fixes.json"
out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(f"wrote {out} ({len(TASKS)} tasks)")
