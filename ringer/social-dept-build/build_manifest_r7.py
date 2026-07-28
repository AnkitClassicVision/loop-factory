#!/usr/bin/env python3
"""Build manifest-r7-driver.json — integration fix: social_daily.sh invents node
CLI args that don't exist (parallel lanes diverged). Fix the driver to the REAL
interfaces and add an executed end-to-end driver test with fakes."""
import json
from pathlib import Path

REPO = "/mnt/d_drive/repos/loop-factory"
WORKDIR = "/home/ankit114/ringer-work/social-dept-build-r7"
CHECK = "/home/ankit114/repos/ringer/templates/fix-swarm/checks/fix-swarm.py"

OWNED = [
    "departments/social/runtime/social_daily.sh",
    "departments/social/tests/test_publish_chain.py",
]
VERIFY = "PYTHONDONTWRITEBYTECODE=1 python3 -m pytest departments/social/tests/ -q -p no:cacheprovider"

SPEC = f"""You are a fix worker repairing the social department's daily driver. Your current working directory IS a git worktree of /mnt/d_drive/repos/loop-factory at commit e94bd9e — edit files directly, no git commit/branch/push, no skills/MCP, no network, tests use fakes only. Own ONLY: {', '.join(OWNED)}. All other runtime nodes are READ-ONLY — the driver adapts to THEM, never the reverse.

DEFECT: departments/social/runtime/social_daily.sh was written against guessed node interfaces. Verified real interfaces (read each node's argparse to confirm before editing):
- inventory_backcatalog.py: --state-dir --out [--rss (append)] [--items (append)] [--index]. NOTE: it writes the refreshed index rows to --out; the driver must install that output as the canonical index for downstream nodes (e.g. run it with --index <state index> for merge, then copy/atomic-replace its --out rows into the state index path before S1/N2 consume it).
- guards.py resolve: --state-dir --item --index --surface --out. Determine from reading guards.py what --item expects; per the procedural graph the order is N1 inventory -> S1 resolve -> N2 select. If guards.resolve validates a single candidate item rather than an index refresh receipt, run S1 AFTER N2 on the selected candidate (graph lists S1 before N2, but resolve's real contract wins — document the choice in a comment referencing what resolve actually validates).
- select_candidate.py: --state-dir --index --out [--suppression] [--cooldown-days] [--as-of]. It reads the INDEX (not a resolve receipt).
- guards.py eligibility: --state-dir --item --suppression --approvals --out (confirm exact names by reading).
- assemble_context.py: --state-dir --candidate --brand --offer(confirm) --out [--version].
- guards.py privacy: --state-dir --manifest --blocklist --out (confirm).
- kernel_bridge.py authorize-model / authorize-dispatch: confirm subcommand args by reading kernel_bridge.py main.
- draft_post.py: --state-dir --out --bundle --surface --engine --engines-file [--charter] [--prior-draft] [--qa-report] [--round?] [--no-kernel] [--engine-timeout]. There is NO --model-token and NO --defects arg: revision consumes --prior-draft + --qa-report.
- qa_post.py: --state-dir --out --draft --bundle --engine --engines-file [--charter] [--no-kernel] [--engine-timeout].
- dispatch.py: --state-dir --out --draft --qa-report --token --surface-counts [--simulate-sink] [--delivery-mode] [--zernio-cmd] [--i-am-promoted].
- delivery_verify.py: --state-dir --out --receipt [--simulate-sink] [--zernio-cmd].
- record.py: --node --payload --state-dir --out [--intended-epoch] [--lock-timeout] [--shadow/--live] (confirm; keep shadow).

TASKS:
1. Rewrite the node invocations in social_daily.sh to the REAL interfaces above (verify every flag by reading the node source first — the list above is a map, the source is the truth). Add env-overridable SOCIAL_DRAFT_ENGINE (default codex_oauth), SOCIAL_QA_ENGINE (default claude_subscription — MUST differ from draft engine), SOCIAL_ENGINES_FILE (default ${{STATE_DIR}}/engines.yaml), SOCIAL_ENGINE_TIMEOUT (default 300). Keep: set -euo pipefail, receipt gating via run_step for EVERY step (any new/reordered step gets its own receipt), the S6/S7 pre-dispatch re-checks, the qa 2-round loop (round 2 = draft_post with --prior-draft + --qa-report), the yielded early-exit, incident_candidates on failures, and the final record step (fenced records via record.py).
2. If dispatch/delivery_verify need --simulate-sink to prove delivered_count==0 in shadow, wire a sink path under the run dir and pass it to BOTH so verification checks the sink's record (read both nodes to confirm the shared-sink contract).
3. Test (in test_publish_chain.py): a REAL end-to-end driver execution — run social_daily.sh via subprocess with a tmp state dir seeded with: a 2-item index (with body files), empty suppression, approvals/blocklist yamls, empty observations, surface_counts at zero, brand/offer jsons, an engines.yaml whose two engines are tmp FAKE scripts (draft fake emits a valid draft JSON with sources; qa fake emits pass:true report), fake zernio never invoked. Assert: exit 0; every expected receipt file exists in the run dir; dispatch receipt has simulated:true and delivered_count==0; runs.jsonl gained a row. Keep the existing corrupt-receipt chain-stop test working (adapt its seeding to the fixed interfaces).

HOW TO RUN (this is also the check): {VERIFY}

OUTPUT CONTRACT: fixed driver + tests green, plus ./fix-summary.md ('# Fix Summary', '## Summary', '## Files Changed', '## Verification', '## Assumptions', <700 words) noting every interface correction you made."""

TASKS = [{
    "key": "fix-driver",
    "engine": "codex",
    "task_type": "code-fix",
    "timeout_s": 5400,
    "engine_args": ["-c", "model_reasoning_effort=high"],
    "spec": SPEC,
    "check": (
        f"python3 '{CHECK}' --verify-command '{VERIFY}' "
        f"--patch '{WORKDIR}/fix-driver.patch' --summary fix-summary.md "
        f"--exported-summary '{WORKDIR}/fix-driver.summary.md' "
        f"--owned-files '{','.join(OWNED)}'"
    ),
    "expect_files": [],
    "verified": "the daily driver invokes every node with its real CLI, and an executed end-to-end test drives the whole chain with fakes proving receipts + delivered_count==0 + chain-stop on corruption",
}]

manifest = {
    "run_name": "social-dept-build",
    "workdir": WORKDIR,
    "max_parallel": 1,
    "worktrees": True,
    "repo": REPO,
    "tasks": TASKS,
}

out = Path(__file__).parent / "manifest-r7-driver.json"
out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(f"wrote {out} (1 task)")
