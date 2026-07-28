#!/usr/bin/env python3
"""Build manifest-r5-verify.json — single cross-model verification lane: confirm
each of the 16 accepted findings is closed at commit 9da43e2 (fix diff vs the
findings list). Claude family verifies Codex fixes."""
import json
from pathlib import Path

REPO = "/mnt/d_drive/repos/loop-factory"
WORKDIR = "/home/ankit114/ringer-work/social-dept-build-r5"
CHECK = "/home/ankit114/repos/ringer/templates/review-swarm/checks/review-swarm.py"

FINDINGS = """1 pull_zernio_analytics: missing platform_verified fabricated as 1.0 → must now quarantine/missing, never invent
2 compare_charter: outage marker rows absorbed as posts_count=0 → must now exit 3 status:missing
3 assemble_weekly_digest: CharterError still produced a digest → must now fail closed exit 3
4 assemble_weekly_digest: raw metric names rendered verbatim (content-leak) → must now allowlist + normalize
5 read_metrics_records: wholesale row copy (self-reports/sensitive keys) → must now source-allowlist + field-whitelist + sanitized:true pack
6 proposal_card_to_outbox: evidence not re-verified at append → must now require --evidence-pack + reject unverifiable
7 proposal_card_to_outbox: --out could write outside state/ incl. governance files → must now constrain + refuse
8 proposal_card_to_outbox: dedup race → must now hold an flock across read-check-append
9 propose_insights: engine allowlist hardcoded → must now load charter budget.engine_allowlist, fail closed when absent
10 proposal_card_to_outbox: TTL hardcoded 24 → must now read charter escalation.no_reply_ttl_hours
11 test_sense_lane: fixture masked fabricated verification → must now assert missing/quarantine behavior
12 test_sense_lane: threshold mutations partial → must now mutate every consumed charter threshold
13 test_publish_chain: all-author cap untested → dispatch must now require --surface-counts, yield at charter cap, tests prove other-author fill yields with zero dispatch
14 test_publish_chain/social_daily: missing-receipt chain stop untested → driver test must now corrupt a receipt and prove the chain stops with an incident entry
15 assemble_context: body_text/title/url never placeholder-checked → TODO_ stub must now flip complete:false + exit 2
16 draft_post/qa_post: engine allowlist + max rounds hardcoded, empty sources[] passed silently → must now use factory/charter_loader accessors (fail closed) and flag empty sources as missing_sources
Also verify the P3: kernel_bridge NON_LIVE_STATES shared constant covers draft_only in require_shadow standalone."""

SPEC = f"""You are a READ-ONLY verification reviewer. Your current working directory IS a git worktree of /mnt/d_drive/repos/loop-factory at commit 9da43e2 (fixes applied). Read files directly; write ONLY ./report.md. No repo modifications, no git commit/branch/push, no skills/MCP, no network, do not execute repo python.

MISSION: verify that each finding below is ACTUALLY CLOSED in the code and covered by a test that would fail if the fix regressed. Use `git show 9da43e2 --stat` and `git diff 7d79b23 9da43e2 -- <file>` to see the fix diff, then read the current code and the named tests. A finding is CLOSED only if (a) the defective behavior is gone, (b) a test exercises the negative case. Report any finding that is unfixed, partially fixed, regressed elsewhere, or test-covered in name only — with file:line evidence. Also flag any NEW defect the fixes introduced that you notice while reading.

FINDINGS TO VERIFY (from three prior reviews):
{FINDINGS}

REPORT CONTRACT (validated mechanically — follow exactly): ./report.md, <=1200 words, starting with '# Review Report', then '## Summary' (max 3 non-empty lines), '## Findings' (ONLY problems: unfixed/partial/regression/test-theater, each as '### Finding: <title>' with 'Evidence: <file:line>', 'Impact: ...', 'Fix: ...', 'Priority: P0|P1|P2|P3', 'Confidence: high|medium|low'; if everything verified closed, write 'No findings'), '## Clean' (list each verified-closed finding number with the test that proves it), '## Assumptions'."""

TASKS = [{
    "key": "verify-fixes",
    "engine": "claude-lean",
    "model": "sonnet",
    "task_type": "code-review",
    "timeout_s": 2400,
    "spec": SPEC,
    "check": (
        f"cp report.md '{WORKDIR}/verify-fixes.report.md' && "
        f"python3 '{CHECK}' --report report.md --surface 'verify-fixes' && "
        "test -z \"$(git status --porcelain -- departments kernel factory | grep -v 'report.md')\""
    ),
    "expect_files": [],
    "verified": "a structurally valid verification report exists confirming (or refuting, with file:line) closure of all 16 findings against the actual diff and tests",
}]

manifest = {
    "run_name": "social-dept-build",
    "workdir": WORKDIR,
    "max_parallel": 1,
    "worktrees": True,
    "repo": REPO,
    "tasks": TASKS,
}

out = Path(__file__).parent / "manifest-r5-verify.json"
out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(f"wrote {out} (1 task)")
