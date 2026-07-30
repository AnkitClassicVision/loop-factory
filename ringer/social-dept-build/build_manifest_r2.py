#!/usr/bin/env python3
"""Build manifest-r2-review.json — read-only cross-model review swarm over the
F3 runtime nodes committed at 7d79b23. Claude lanes review Codex-built code;
Codex reviews the Claude-built sense lane + all tests. Same run_name (one job,
one artifact)."""
import json
from pathlib import Path

REPO = "/mnt/d_drive/repos/loop-factory"
WORKDIR = "/home/ankit114/ringer-work/social-dept-build-r2"
CHECK = "/home/ankit114/repos/ringer/templates/review-swarm/checks/review-swarm.py"

COMMON = f"""You are a READ-ONLY code reviewer. You must NEVER modify, create, or delete any file inside {REPO} — you only read it. Write exactly one file: ./report.md in your current working directory. Do not load skills or call MCP/Apps. No network. Do not run python against the repo (reading files is sufficient; execution belongs to the harness).

CONTEXT: {REPO} is the loop-factory. The NEW `social` department (back-catalog social republishing via Zernio, SHADOW mode) was just built at commit 7d79b23. Governing spec (read first):
  departments/social/charter.yaml, departments/social/subgraphs.json,
  departments/social/procedural-graph.md, departments/social/knowledge/concept-map.md
Non-negotiable laws to review against: deny-by-default / fail-closed everywhere; shadow = delivered_count==0 provable; receipts before advancement; caps count ALL authors; drafts consume ONLY sanitized bundles; cross-model QA; no secrets/PHI; charter values never hardcoded; governance files never written by runtime code.

REPORT CONTRACT (validated mechanically — follow exactly): ./report.md, <=1200 words, starting with '# Review Report', then '## Summary' (max 3 non-empty lines), '## Findings' (each as '### Finding: <title>' with lines 'Evidence: <file:line>', 'Impact: ...', 'Fix: ...', 'Priority: P0|P1|P2|P3', 'Confidence: high|medium|low'; if none, write 'No findings'), '## Clean' (dimensions reviewed with no findings), '## Assumptions'. Findings are DEFECTS ONLY — no praise, no style nits below P3 unless they mask a real defect."""


VERIFIED = {
    "review-spine-failclosed": "a structurally valid defect report on the publish spine exists: every finding cites file:line with impact, fix, priority, confidence",
    "review-content-contracts": "a structurally valid defect report on the content pipeline's charter conformance exists, findings cited to file:line",
    "review-sense-learn-tests": "a structurally valid defect report on the sense/learn lanes and test quality exists, findings cited to file:line",
}


def task(key, engine, spec, extra=None):
    t = {
        "key": key,
        "engine": engine,
        "task_type": "code-review",
        "timeout_s": 2400,
        "spec": spec,
        "check": f"python3 '{CHECK}' --report report.md --surface '{key}'",
        "expect_files": ["report.md"],
        "verified": VERIFIED[key],
    }
    if engine == "claude-lean":
        t["model"] = "sonnet"
    if extra:
        t.update(extra)
    return t


TASKS = [
    task(
        "review-spine-failclosed",
        "claude-lean",
        f"""{COMMON}

YOUR LENS: fail-closed integrity of the publish spine (Codex-built; you are the cross-model check).
Files under review: departments/social/runtime/guards.py, dispatch.py, delivery_verify.py, record.py, kernel_bridge.py, social_daily.sh, and their tests test_guards.py, test_publish_chain.py, test_records_integrity.py.
Hunt specifically for: (1) any path where an error, missing file, malformed JSON, or unexpected state lets execution CONTINUE instead of blocking (fail-open); (2) shadow-law escapes — any way dispatch could reach a real `zernio` invocation while autonomy_state is shadow, incl. env/flag combinations; (3) receipt gaps — steps in social_daily.sh that can advance without their receipt existing, or receipts written before the proving action completes; (4) KILLED/BREAKER marker races or bypasses (TOCTOU, marker checked in one place but not another); (5) delivery_verify trusting scheduler-echoed data anywhere; (6) record.py fenced-order violations vs the podcast precedent (departments/podcast/runtime/record.py); (7) secrets/credentials/PHI leaking into records or logs.""",
    ),
    task(
        "review-content-contracts",
        "claude-lean",
        f"""{COMMON}

YOUR LENS: charter-contract conformance of the content pipeline (Codex-built; you are the cross-model check).
Files under review: departments/social/runtime/draft_post.py, qa_post.py, engines.example.yaml, inventory_backcatalog.py, select_candidate.py, assemble_context.py, knowledge/brand-context.example.yaml, and tests test_llm_nodes.py, test_republish_scripts.py.
Hunt specifically for: (1) any way draft_post can consume a bundle whose sanitized flag is not literally true; (2) engine-allowlist bypasses (names beyond codex_oauth/claude_subscription accepted, or argv injection through the engines yaml); (3) cross-model enforcement gaps in qa_post (same-engine drafts slipping through); (4) QA fail-open: engine crash, malformed model output, or JSON parse failure resulting in pass:true or missing defects; (5) nondeterminism in select_candidate (set iteration, dict ordering, time.now leaks into ranking beyond the documented age term); (6) completeness-gate gaps in assemble_context (TODO_ or empty fields that do NOT flip complete:false); (7) charter values hardcoded instead of read from charter.yaml (caps, banned words vs charter floors, engine names); (8) the grounded-sources check being satisfiable by trivial/empty sources[].""",
    ),
    task(
        "review-sense-learn-tests",
        "codex",
        f"""{COMMON}

YOUR LENS: the sensing/learn lanes (Claude-built — you are the cross-model check) plus TEST QUALITY across the whole department.
Files under review: departments/social/runtime/pull_zernio_analytics.py, pull_call_joins.py, compare_charter.py, assemble_weekly_digest.py, read_metrics_records.py, propose_insights.py, proposal_card_to_outbox.py, and ALL seven test files in departments/social/tests/.
Hunt specifically for: (1) fabricated-data paths — any branch where a missing/failed feed yields zeros or invented values instead of status:missing + exit 3; (2) independence violations — compare_charter or read_metrics_records consuming the department's self-reported claims instead of observation rows; (3) digest leaking DM/comment bodies or unsanitized content; (4) ungrounded-proposal validation gaps in propose_insights (evidence ids not actually verified against the pack); (5) outbox writes anywhere beyond state/approval_queue.jsonl, or non-idempotent appends; (6) TEST THEATER across all seven test files — tests that cannot fail, fakes that bypass the contract under test, assertions on your own fixture instead of the code's behavior, missing negative tests for the fail-closed laws (name the specific missing case); (7) charter-threshold tests that would still pass if thresholds were hardcoded.""",
        {"engine_args": ["-c", "model_reasoning_effort=high"]},
    ),
]

manifest = {
    "run_name": "social-dept-build",
    "workdir": WORKDIR,
    "max_parallel": 3,
    "worktrees": False,
    "repo": REPO,
    "tasks": TASKS,
}

out = Path(__file__).parent / "manifest-r2-review.json"
out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(f"wrote {out} ({len(TASKS)} tasks)")
