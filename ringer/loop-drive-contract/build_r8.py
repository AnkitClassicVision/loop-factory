#!/usr/bin/env python3
"""Generate manifest-r8-review.json: cross-model read-only review of wave 1.

Review before integration, never the builder (codex built both patches; claude
reviews them). Four rounds of this job passed their executed check and were
rejected in coordinator review — the review lane exists because checks written
from the DEFECT's failure modes miss the ones the FIX invents.

Edit LANES, then:  python3 build_r8.py
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
PODCAST = "/mnt/d_drive/repos/podcast"
R7 = "/mnt/d_drive/ringer-work/loop-drive-contract-r7"
R7B = "/mnt/d_drive/ringer-work/loop-drive-contract-r7b"
WORKDIR = "/mnt/d_drive/ringer-work/loop-drive-contract-r8"
CHECKS = f"{HERE}/checks"

COMMON = """You are a READ-ONLY cross-model reviewer (the builder was codex; you are not).
You own exactly one output file, named at the end. Read anything named here; write
nothing else, run no git mutations, no commits, no sends, no skills, no MCP.

Your job is NOT style. Four patches in this job passed their executed check and
were then rejected in coordinator review, every time for a defect the FIX
invented that the check could not see. Hunt for exactly that class: what NEW way
can this mechanism fail that the old mechanism could not?

Verdict standard: QA: PASS only if you would bet a live production loop on it.
Concrete doubts = QA: REVISE with the exact file:line and the failing input.
Broken = QA: BLOCK. Cite at least 2 concrete file:line observations either way."""


def u4_review_spec() -> str:
    return f"""{COMMON}

SUBJECT: the U4 re-entry patch for the podcast loop runner.

Read, in order:
1. The patch: {R7B}/r7b-u4-verify.patch (confined to scripts/run_podcast_loop.sh
   and scripts/obe_loop_verdict.py)
2. The patched tree, for full context: {R7}/r7-u4-reentry/ (a git worktree with
   the patch applied, uncommitted)
3. The builder's summary: {R7B}/r7b-u4-verify.summary.md
4. The base files at {PODCAST}/scripts/ for comparison.

The contract the patch claims to implement: on a FAILED verdict the runner
re-enters the same day under the same run identity (same receipt and QA paths),
folding the superseded attempt into the same receipt with its loop-drive-v1
fence defused so exactly one live block remains; every Ringer invocation appends
a row to a weekly worker-minutes ledger at
$LOOP_RECEIPT_DIR/worker-minutes-<%G-W%V>.jsonl; before the first invocation and
every re-entry a preflight refuses to run once the week's ledger crosses 1680
minutes, stamping CAPPED via a new runner-observed --cap flag; --reentry N
maintains a 'REENTRY: N' line-2 stamp; DROVE-with-quota, EXHAUSTED, CAPPED and
BLOCKED do not re-enter; a nonzero ringer exit on any pass stays the loud
existing failure path.

Hunt list (each one is a class that survived an executed check before):
- Shell quoting and word-splitting in every NEW line of run_podcast_loop.sh —
  this exact script died once from 36 unescaped quotes that `bash -n` passed.
  Look at every variable expansion in the new re-entry loop, especially anything
  interpolating a verdict REASON string into the regenerated worker spec.
- Loop termination: is there ANY path where reentry_allowed stays true and no
  ceiling, no attempts-exhaustion and no rc!=0 stops it? What happens on a
  malformed verdict JSON mid-loop (jq/python parse failure)?
- The receipt fold: can the history-defusing rewrite ever eat the VERDICT line,
  duplicate the live block, or corrupt a receipt whose body contains backticks?
  (An earlier apply_verdict overwrote line 0 unconditionally and deleted the
  title — same file, same class.)
- Ledger arithmetic: ISO week boundary (%G-W%V vs %Y-%U), float seconds,
  concurrent loops appending to the same weekly file, a corrupt ledger line.
- Flag interactions in obe_loop_verdict.py: --cap with --qa-verdict REVISE
  (which wins?), --cap with an invalid ceiling name, --reentry without --apply,
  backward compatibility of every existing call site (grep for them).
- The preflight CAPPED receipt: does the runner-authored receipt satisfy
  loop_receipt_check.sh (line-1 verdict in head -5, >=600 bytes, evidence
  language, no secret-shaped strings)?
- Anything the patch touches that the contract says must NOT change: escalation
  digest markers, budget guard, traps, referral preflight, QA case exits.

OUTPUT (mandatory): write {WORKDIR}/u4-review.md — FIRST LINE exactly
'QA: PASS' or 'QA: REVISE' or 'QA: BLOCK', then your findings with file:line
evidence, most severe first."""


def u7_review_spec() -> str:
    return f"""{COMMON}

SUBJECT: the U7 prose-gates module (new files, not yet wired into anything).

Read, in order:
1. The patch: {R7}/r7-u7-gates.patch (creates server/pipeline/prose_gates.py and
   tests/test_prose_gates.py)
2. The builder's summary: {R7}/r7-u7-gates.summary.md
3. For context on how the gates will be consumed later:
   {PODCAST}/scripts/obe_draft_voice_qa.py (the repair loop shape) and
   {PODCAST}/server/pipeline/source_room.py (existing staleness logic).

The contract: python3 -m server.pipeline.prose_gates --gate <name> --input
<json>; exit 0 pass, exit 2 block with a one-object JSON stdout line carrying
gate/violation/offending_span/fix_hint (all non-empty, gate matching), exit 3
unknown gate or unreadable input; stdlib only; writes nothing; fail closed.
Four gates: source_truth_resolved_before_intake (manifest freshness vs
max_age_days, missing/garbled/undated blocks), channel_rule (cold never
email/text, unknown temperature or channel blocks), neutralize_preexisting
nominated (nominated + email_present + not cleared_by_human blocks),
cross_model_qa_pass_before_done (first line exactly 'QA: PASS', empty model
names block, worker==qa case-insensitive blocks).

Hunt list:
- Fail-open holes: any input shape (wrong types, null, missing keys, list
  instead of dict) that reaches exit 0 without positive verification.
- Timezone bugs in the freshness math (naive vs aware datetimes; a manifest
  dated in a non-UTC offset).
- The QA-file first-line check: trailing whitespace, BOM, CRLF, 'QA: PASS extra
  text' — which of these pass, and should they?
- Exit-code discipline: does any error path raise a traceback (exit 1) instead
  of the contract's 2 or 3? Run the module yourself on hostile inputs — you may
  execute `python3 -m server.pipeline.prose_gates` inside {R7}/r7-u7-gates/
  worktree if it still exists, or import-check the file from the patch.
- Side effects: any write, network, or subprocess anywhere.
- Test honesty: do the tests import and exercise the module, or shell out /
  restate the fixtures without asserting behavior?

OUTPUT (mandatory): write {WORKDIR}/u7-review.md — FIRST LINE exactly
'QA: PASS' or 'QA: REVISE' or 'QA: BLOCK', then your findings with file:line
evidence, most severe first."""


LANES = [
    ("u4", "claude", u4_review_spec(), "u4-review.md"),
    ("u7", "claude", u7_review_spec(), "u7-review.md"),
]

tasks = []
for unit, engine, spec, report in LANES:
    tasks.append({
        "key": f"r8-{unit}-review",
        "engine": engine,
        "task_type": "code-review",
        "timeout_s": 1500,
        "spec": spec,
        "engine_args": [
            f"--add-dir={R7}", f"--add-dir={R7B}", f"--add-dir={PODCAST}",
            f"--add-dir={WORKDIR}",
        ],
        "check": (
            f"bash -c 'head -1 {WORKDIR}/{report} 2>/dev/null | grep -E \"^QA: (PASS|REVISE|BLOCK)$\" "
            f"|| {{ echo \"CHECK FAIL: no verdict line at {WORKDIR}/{report}\"; exit 1; }}; "
            f"grep -cE \"[a-z_]+\\.(py|sh):[0-9]+\" {WORKDIR}/{report} | awk '\"'\"'{{ if ($1<2) {{ print \"CHECK FAIL: fewer than 2 file:line citations\"; exit 1 }} else print \"CHECK PASS: verdict + citations present\" }}'\"'\"''"
        ),
        "expect_files": [f"{WORKDIR}/{report}"],
        "verified": "a real cross-model verdict with file:line evidence exists; the coordinator reads the content before anything lands",
    })

manifest = {
    "run_name": "loop-drive-contract",
    "workdir": WORKDIR,
    "max_parallel": 2,
    "tasks": tasks,
}

out = HERE / "manifest-r8-review.json"
out.write_text(json.dumps(manifest, indent=2) + "\n")
print(f"wrote {out}")
for task in manifest["tasks"]:
    print(f"  {task['key']:<16} {task['engine']:<8} spec={len(task['spec'])} chars")
