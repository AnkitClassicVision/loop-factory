#!/usr/bin/env python3
"""Build manifest-r8-sources.json — prompt/validator contract fix for source
grounding, found by the first REAL shadow run: the model cannot know which
source identifiers the validator will accept because the prompt never says."""
import json
from pathlib import Path

REPO = "/mnt/d_drive/repos/loop-factory"
WORKDIR = "/home/ankit114/ringer-work/social-dept-build-r8"
CHECK = "/home/ankit114/repos/ringer/templates/fix-swarm/checks/fix-swarm.py"

OWNED = [
    "departments/social/runtime/draft_post.py",
    "departments/social/runtime/qa_post.py",
    "departments/social/tests/test_llm_nodes.py",
]
VERIFY = "PYTHONDONTWRITEBYTECODE=1 python3 -m pytest departments/social/tests/ -q -p no:cacheprovider"

SPEC = f"""You are a fix worker on the social department's model nodes. Your current working directory IS a git worktree of /mnt/d_drive/repos/loop-factory at commit 7fb6a25 — edit files directly, no git commit/branch/push, no skills/MCP, no network, tests use fakes only. Own ONLY: {', '.join(OWNED)}.

DEFECT (found by the first real shadow run, receipts at departments/social/state/receipts/20260728T193403Z-851931 — read-only evidence): draft_post's grounding validator (draft_post.py:362, `source_ref not in bundle_strings`) requires each draft sources[].source to EXACTLY equal some string value in the sanitized bundle, but the drafting prompt never tells the model which identifiers are valid. A real Codex draft cited four sources and all four were rejected ('is not present in the bundle'), blocking the chain. qa_post.py mirrors the same check (~lines 322-330). The validator and the prompt must share one explicit contract.

TASKS:
1. In draft_post.py, derive ALLOWED_SOURCE_IDS from the sanitized bundle: the item's url, the item's item_id, and the item's title (exact strings), plus the offer cta_url for CTA-related claims. Put them in the drafting prompt VERBATIM as an enumerated list with the instruction: every sources[].source MUST be exactly one of these identifiers; every factual claim and every number in the body must appear in a sources[].claim. Change the validator to check membership in ALLOWED_SOURCE_IDS (computed the same way) instead of the whole-bundle string sweep. Same contract in --revise mode.
2. In qa_post.py, align the mirrored deterministic source check to the same ALLOWED_SOURCE_IDS derivation (shared derivation logic may be duplicated inline per the no-cross-import rule; keep the two implementations trivially identical and comment that they mirror each other).
3. Keep everything else fail-closed and unchanged: missing_sources on empty list, ungrounded-number check, one-CTA rule, sanitized-only input, allowlist, cross-model refusal.
4. Tests (test_llm_nodes.py): fake engine emitting sources with the item url → passes; with item_id → passes; with title → passes; with free text ('episode description') → blocked naming the invalid ref; prompt file written to the engine actually CONTAINS the enumerated identifiers (assert the fake engine's captured prompt includes the item url and the instruction text); qa_post mirror behaves identically on the same drafts.

HOW TO RUN (this is also the check): {VERIFY}

OUTPUT CONTRACT: fixes + tests green, plus ./fix-summary.md ('# Fix Summary', '## Summary', '## Files Changed', '## Verification', '## Assumptions', <700 words)."""

TASKS = [{
    "key": "fix-sources-contract",
    "engine": "codex",
    "task_type": "code-fix",
    "timeout_s": 3600,
    "engine_args": ["-c", "model_reasoning_effort=high"],
    "spec": SPEC,
    "check": (
        f"python3 '{CHECK}' --verify-command '{VERIFY}' "
        f"--patch '{WORKDIR}/fix-sources-contract.patch' --summary fix-summary.md "
        f"--exported-summary '{WORKDIR}/fix-sources-contract.summary.md' "
        f"--owned-files '{','.join(OWNED)}'"
    ),
    "expect_files": [],
    "verified": "the drafting prompt enumerates the exact valid source identifiers and both validators check that same enumerated set, proven by tests incl. a captured-prompt assertion",
}]

manifest = {
    "run_name": "social-dept-build",
    "workdir": WORKDIR,
    "max_parallel": 1,
    "worktrees": True,
    "repo": REPO,
    "tasks": TASKS,
}

out = Path(__file__).parent / "manifest-r8-sources.json"
out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(f"wrote {out} (1 task)")
