#!/usr/bin/env python3
"""Build manifest-r3-review-fix.json — re-run of the two Claude-lens reviews.
r2 lesson (harness-config): with worktrees:false, claude-lean is sandbox-confined
to its taskdir and cannot read the repo; both Claude lanes correctly refused to
fabricate. Fix: worktrees:true so cwd IS the repo checkout; the check exports the
report to the workdir before validating (worktrees can be cleaned on pass)."""
import json
from pathlib import Path

REPO = "/mnt/d_drive/repos/loop-factory"
WORKDIR = "/home/ankit114/ringer-work/social-dept-build-r3"
CHECK = "/home/ankit114/repos/ringer/templates/review-swarm/checks/review-swarm.py"

COMMON = """You are a READ-ONLY code reviewer. Your current working directory IS a git worktree of /mnt/d_drive/repos/loop-factory at commit 7d79b23 — read files directly via relative paths. You must NOT modify, create, or delete ANY repo file; the ONLY file you write is ./report.md in the worktree root. Do not git commit/branch/push. Do not load skills or call MCP/Apps. No network. Do not execute repo python (read; the harness executes).

CONTEXT: this repo is the loop-factory. The NEW `social` department (back-catalog social republishing via Zernio, SHADOW mode) was just built. Governing spec (read first):
  departments/social/charter.yaml, departments/social/subgraphs.json,
  departments/social/procedural-graph.md, departments/social/knowledge/concept-map.md
Non-negotiable laws to review against: deny-by-default / fail-closed everywhere; shadow = delivered_count==0 provable; receipts before advancement; caps count ALL authors; drafts consume ONLY sanitized bundles; cross-model QA; no secrets/PHI; charter values never hardcoded; governance files never written by runtime code.

REPORT CONTRACT (validated mechanically — follow exactly): ./report.md, <=1200 words, starting with '# Review Report', then '## Summary' (max 3 non-empty lines), '## Findings' (each as '### Finding: <title>' with lines 'Evidence: <file:line>', 'Impact: ...', 'Fix: ...', 'Priority: P0|P1|P2|P3', 'Confidence: high|medium|low'; if none, write 'No findings'), '## Clean' (dimensions reviewed with no findings), '## Assumptions'. Findings are DEFECTS ONLY — no praise, no style nits below P3 unless they mask a real defect."""

SPECS = {
    "review-spine-failclosed": f"""{COMMON}

YOUR LENS: fail-closed integrity of the publish spine (Codex-built; you are the cross-model check).
Files under review: departments/social/runtime/guards.py, dispatch.py, delivery_verify.py, record.py, kernel_bridge.py, social_daily.sh, and their tests test_guards.py, test_publish_chain.py, test_records_integrity.py.
Hunt specifically for: (1) any path where an error, missing file, malformed JSON, or unexpected state lets execution CONTINUE instead of blocking (fail-open); (2) shadow-law escapes — any way dispatch could reach a real `zernio` invocation while autonomy_state is shadow, incl. env/flag combinations; (3) receipt gaps — steps in social_daily.sh that can advance without their receipt existing, or receipts written before the proving action completes; (4) KILLED/BREAKER marker races or bypasses (TOCTOU, marker checked in one place but not another); (5) delivery_verify trusting scheduler-echoed data anywhere; (6) record.py fenced-order violations vs the podcast precedent (departments/podcast/runtime/record.py); (7) secrets/credentials/PHI leaking into records or logs.""",
    "review-content-contracts": f"""{COMMON}

YOUR LENS: charter-contract conformance of the content pipeline (Codex-built; you are the cross-model check).
Files under review: departments/social/runtime/draft_post.py, qa_post.py, engines.example.yaml, inventory_backcatalog.py, select_candidate.py, assemble_context.py, knowledge/brand-context.example.yaml, and tests test_llm_nodes.py, test_republish_scripts.py.
Hunt specifically for: (1) any way draft_post can consume a bundle whose sanitized flag is not literally true; (2) engine-allowlist bypasses (names beyond codex_oauth/claude_subscription accepted, or argv injection through the engines yaml); (3) cross-model enforcement gaps in qa_post (same-engine drafts slipping through); (4) QA fail-open: engine crash, malformed model output, or JSON parse failure resulting in pass:true or missing defects; (5) nondeterminism in select_candidate (set iteration, dict ordering, time.now leaks into ranking beyond the documented age term); (6) completeness-gate gaps in assemble_context (TODO_ or empty fields that do NOT flip complete:false); (7) charter values hardcoded instead of read from charter.yaml (caps, banned words vs charter floors, engine names); (8) the grounded-sources check being satisfiable by trivial/empty sources[].""",
}

VERIFIED = {
    "review-spine-failclosed": "a structurally valid defect report on the publish spine, written from the actual code in a worktree, exported to the workdir",
    "review-content-contracts": "a structurally valid defect report on the content pipeline's charter conformance, written from the actual code in a worktree, exported to the workdir",
}

TASKS = [
    {
        "key": key,
        "engine": "claude-lean",
        "model": "sonnet",
        "task_type": "code-review",
        "timeout_s": 2400,
        "spec": spec,
        "check": (
            f"cp report.md '{WORKDIR}/{key}.report.md' && "
            f"python3 '{CHECK}' --report report.md --surface '{key}' && "
            "test -z \"$(git status --porcelain -- departments kernel factory | grep -v 'report.md')\""
        ),
        "expect_files": [],
        "verified": VERIFIED[key],
    }
    for key, spec in SPECS.items()
]

manifest = {
    "run_name": "social-dept-build",
    "workdir": WORKDIR,
    "max_parallel": 2,
    "worktrees": True,
    "repo": REPO,
    "tasks": TASKS,
}

out = Path(__file__).parent / "manifest-r3-review-fix.json"
out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(f"wrote {out} ({len(TASKS)} tasks)")
