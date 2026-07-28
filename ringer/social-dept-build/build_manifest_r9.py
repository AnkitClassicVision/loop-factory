#!/usr/bin/env python3
"""Build manifest-r9-adapter.json — engine adapter passes a temp-file PATH as
the CLI prompt argument; agentic Codex read it by luck, the Claude lane
answered the path with prose (no JSON). Substitute prompt CONTENT instead, and
stop charging QA edit rounds for engine outages (charter transient policy)."""
import json
from pathlib import Path

REPO = "/mnt/d_drive/repos/loop-factory"
WORKDIR = "/home/ankit114/ringer-work/social-dept-build-r9"
CHECK = "/home/ankit114/repos/ringer/templates/fix-swarm/checks/fix-swarm.py"

OWNED = [
    "departments/social/runtime/draft_post.py",
    "departments/social/runtime/qa_post.py",
    "departments/social/runtime/propose_insights.py",
    "departments/social/runtime/engines.example.yaml",
    "departments/social/runtime/social_daily.sh",
    "departments/social/tests/test_llm_nodes.py",
    "departments/social/tests/test_learn_lane.py",
    "departments/social/tests/test_publish_chain.py",
]
VERIFY = "PYTHONDONTWRITEBYTECODE=1 python3 -m pytest departments/social/tests/ -q -p no:cacheprovider"

SPEC = f"""You are a fix worker on the social department's engine adapters and daily driver. Your current working directory IS a git worktree of /mnt/d_drive/repos/loop-factory at commit cf964cc — edit files directly, no git commit/branch/push, no skills/MCP, no network, tests use fakes only. Own ONLY: {', '.join(OWNED)}.

DEFECTS (found by real shadow run, receipts at departments/social/state/receipts/20260728T194215Z-913954, read-only evidence):
A. The engine adapter in draft_post.py/qa_post.py/propose_insights.py (_call_engine/_engine_argv) writes the prompt to a temp file and substitutes the file PATH into the argv template ({{prompt_file}}). CLIs like `claude -p <arg>` and `codex exec <arg>` treat that positional as the PROMPT STRING — the Claude QA lane received a literal path and answered with prose (no JSON → qa_engine_unavailable twice). Codex only worked because it agentically read the file.
B. social_daily.sh charges a QA edit round when the qa_report's only defect is qa_engine_unavailable — an engine outage is a TRANSIENT failure (charter exceptions.transient_failure: retry 3x with backoff), not a content defect; the run burned both edit rounds on a dead lane and quarantined a possibly-fine draft.

TASKS:
1. Adapter (all three model nodes, keeping their no-cross-import mirrored-implementation style): support a {{prompt}} placeholder that substitutes the PROMPT CONTENT itself as a single argv element (exactly how Ringer passes specs). Keep {{prompt_file}} working for back-compat but make {{prompt}} the documented default. A template containing NEITHER placeholder is a config error (fail closed). Update engines.example.yaml to use {{prompt}} for both engines (claude lane: claude -p --disable-slash-commands --exclude-dynamic-system-prompt-sections --output-format text --no-session-persistence {{prompt}}; codex lane: codex exec --sandbox read-only {{prompt}}).
2. Driver (social_daily.sh): when a qa_report fails with ONLY qa_engine_unavailable defects, do NOT consume an edit round — retry the SAME qa step up to 3 times total (fresh receipt file per attempt, e.g. N5-qa-r1-try2), with a short sleep backoff; if still engine-unavailable after 3 tries, quarantine with failure_class engine_unavailable (distinct from qa_non_convergence) in the incident entry. Content defects keep the existing 2-round edit loop unchanged.
3. Tests: adapter — {{prompt}} template receives the full prompt text as one argv element (fake engine script asserts its $1 contains a known marker from the bundle, not a path); {{prompt_file}} still works; template with neither placeholder is rejected. Driver — fake qa engine that fails twice with garbage then succeeds: chain completes without consuming an edit round; fake qa engine that always emits garbage: quarantine with engine_unavailable failure class after 3 tries; existing content-defect 2-round behavior unchanged.

HOW TO RUN (this is also the check): {VERIFY}

OUTPUT CONTRACT: fixes + tests green, plus ./fix-summary.md ('# Fix Summary', '## Summary', '## Files Changed', '## Verification', '## Assumptions', <700 words)."""

TASKS = [{
    "key": "fix-engine-adapter",
    "engine": "codex",
    "task_type": "code-fix",
    "timeout_s": 5400,
    "engine_args": ["-c", "model_reasoning_effort=high"],
    "spec": SPEC,
    "check": (
        f"python3 '{CHECK}' --verify-command '{VERIFY}' "
        f"--patch '{WORKDIR}/fix-engine-adapter.patch' --summary fix-summary.md "
        f"--exported-summary '{WORKDIR}/fix-engine-adapter.summary.md' "
        f"--owned-files '{','.join(OWNED)}'"
    ),
    "expect_files": [],
    "verified": "engines receive prompt CONTENT (not a path) proven by a captured-argv test, and QA engine outages retry as transients instead of burning edit rounds",
}]

manifest = {
    "run_name": "social-dept-build",
    "workdir": WORKDIR,
    "max_parallel": 1,
    "worktrees": True,
    "repo": REPO,
    "tasks": TASKS,
}

out = Path(__file__).parent / "manifest-r9-adapter.json"
out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(f"wrote {out} (1 task)")
