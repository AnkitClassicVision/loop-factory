#!/usr/bin/env python3
"""Generate manifest-r13-inputs.json — U8 revalidator + U9 candidate feeder.

These are the two units that decide whether any outreach happens at all. Without
U8 the freshness gate blocks every candidate forever; without U9 the producer is
handed nothing and honestly reports no_candidate. Both blocks look legitimate,
which is the silent-failure shape this job exists to eliminate.

Check proven RED before this manifest was generated: both scripts absent.

Edit LANES, then:  python3 build_r13.py
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
PODCAST = "/mnt/d_drive/repos/podcast"
CHECK = f"{HERE}/checks/u8_u9_inputs_check.py"
WRAPPER = f"{HERE}/checks/wave2_export_check.py"
ROUND = "r13"
WORKDIR = f"/mnt/d_drive/ringer-work/loop-drive-contract-{ROUND}"

BOUNDARY = f"""You are a fix worker. Your current working directory IS a dedicated git
worktree of the repository {PODCAST}, detached at HEAD — edit files here directly.

BOUNDARY, read this before anything else.

This repository sends real email. A bridge service turns podcast Gmail drafts
into approval cards every 30 minutes with --execute, and an approved-send
executor SENDS every 10 minutes with --execute, autosending QA-passed podcast
outreach without human approval under an owner promotion. Your output decides who
receives mail. Treat every line as one step from a live send.

Never execute scripts/run_podcast_loop.sh. Never ssh. Never send a Telegram
message. Never run anything that acts against Gmail, HubSpot or Linear. Never
create or edit systemd units. Never touch ~/.config/ringer or .git. Do not load
skills; do not call MCP tools or Apps. Do not git commit, branch, checkout, stash
or push — leave work uncommitted; the validator exports a patch."""


def u8_spec() -> str:
    return f"""{BOUNDARY}

You own exactly ONE new tracked path: `scripts/source_truth_revalidate.py`. You
may also create ./fix-summary.md. Change nothing else.

WHY. `server/pipeline/prose_gates.py` has a gate,
`source_truth_resolved_before_intake`, that blocks candidate intake unless a
source-truth receipt is fresh. The runner passes
`process/proofs/source_truth_revalidation.json`, and NOTHING WRITES THAT FILE, so
the gate blocks every candidate forever and the block looks legitimate. Your
script is what writes it.

The human-certified authority manifest is
`process/proofs/source_room_authority_manifest.json`. Human judgment stays human:
you RE-VERIFY the observables, you never re-certify authority ranks or room state.

MEASURED FACTS you must design around (verified 2026-08-10):
  * The manifest has 8 sources. Keys per source: source_id, location_ref,
    authority_rank, observed_at, status, allowed_use, data_class, claim_key,
    claim_value. There is NO hash field anywhere; you are establishing the
    baseline.
  * TWO location_ref values are not single file paths. One is a glob
    (`process/nodes/*.aac.json`). One is a semicolon-joined list of five test
    files. **A naive existence check reports permanent drift on those two, the
    gate blocks intake forever, and it looks legitimate.** Handle both forms:
    expand a glob, split on `;` and strip whitespace, and treat the ref as
    satisfied when the expansion resolves to at least one existing file.

WHAT TO BUILD. CLI: `--out <path>` (required), `--manifest <path>` (default the
authority manifest above), `--baseline <path>` (default
`process/proofs/source_truth_baseline.json`), `--simulate-drift <location_ref>`
(test hook: treat that ref as changed without touching any file on disk).

Behavior: for each source, resolve its location_ref into a concrete file list as
described, hash each file with sha256, and compare against the baseline. A ref
whose expansion resolves to zero files, or whose hash differs from a recorded
baseline, is a blocking gap. Record the current hashes back to the baseline so the
first run establishes it and later runs detect change.

Write the receipt to `--out`, mode 0600, as JSON:
  schema "source-truth-revalidation/v1"; generated_at ISO-8601 UTC of THIS run;
  blocking_gaps a list of {{source_id, location_ref, reason}} (empty when clean);
  sources_checked a list of {{source_id, location_ref, files, ok}}.

**The receipt is written on EVERY path, including failure.** Exit 0 when clean,
2 when blocking gaps exist, 1 on an error you could not classify. The receipt,
not the exit code, is the authority.

PRIVACY: the receipt is machine-read and may be quoted in escalations. Paths and
source ids only, never file contents.

HOW TO RUN THE CHECK — exactly what the validator runs:

    PYTHONDONTWRITEBYTECODE=1 python3 {CHECK} --repo "$PWD" --u8-only \\
        --out /tmp/u8-selfcheck

It runs your script, then feeds your receipt to the REAL freshness gate and
requires the gate to PASS a receipt written seconds ago, then uses
`--simulate-drift` on a real single-file source and requires the gate to BLOCK.
It also fails you if a glob or semicolon ref appears in blocking_gaps.

OUTPUT CONTRACT: your new file, uncommitted, plus ./fix-summary.md with
`# Fix Summary`, `## Summary`, `## Files Changed`, `## Verification`,
`## Assumptions`, under 900 words, pasting the real final check output under
Verification. Do not weaken or edit the check."""


def u9_spec() -> str:
    return f"""{BOUNDARY}

You own exactly ONE new tracked path: `scripts/guest_candidate_feed.py`. You may
also create ./fix-summary.md. Change nothing else.

WHY. The runner passes a candidates file to the guest-outreach producer and
NOTHING WRITES IT, so the producer receives an empty list and honestly reports
"no_candidate" on every run. That is indistinguishable from a genuine drought.
Your script is what fills it.

MEASURED FACTS you must design around (verified 2026-08-10):
  * `episodes/FOCUS-LIST.json` is a WORK QUEUE, not a prospect list. Its entries
    are {{due, kind, mode, person_or_episode, priority, reason}} with kinds like
    production_unstick. **Do not read it for candidates.**
  * The real pool is `episodes/CANDIDATE-INBOX.json`, schema candidate-inbox-v1,
    14 entries with keys: name, email, fit_score, confidence, source, evidence,
    note, first_seen. There is NO status or stage field.
  * `episodes/FUNNEL-LEDGER.json` people carry stage, hold, last_touch,
    next_action, provenance and NO email.
  So this unit is a JOIN on name between the inbox (contact + fit) and the ledger
  (state).

THE CONSEQUENTIAL FIELD IS `temperature`, because a cold candidate is barred from
email by the charter and blocked by the channel gate, and this lane autosends.
Derive it from the inbox `source` and `evidence`, never from fit score:
  * warm when the source indicates the person came to us or was referred by
    someone (inbound application, referral, a past guest's nomination, an existing
    conversation);
  * cold otherwise, including any source you cannot classify. **Fail closed.**
Emit `channel: "email"` only for warm. For cold, emit the candidate with
`channel: "postcard"` or omit it entirely; either is acceptable, but a cold
candidate paired with email or text is a hard failure.

EXCLUSIONS, each a hard failure if violated: a person with a non-empty `hold` in
the ledger; a person whose `last_touch` is within 4 days of `--now` (the charter's
per-contact cadence floor); a person whose ledger stage is already published,
closed or rejected; a person with no usable email.

OUTPUT. Write a JSON LIST to `--out`, mode 0600, each entry carrying exactly the
producer's declared shape:
  alias (a stable non-identifying handle, e.g. cand-1), temperature, channel,
  podcast_status, email_present (bool), cleared_by_human (bool), to, subject, body.
Order by fit_score descending. `subject` and `body` are a real, sendable warm
invite: no em dash, no placeholder text, no banned marketing phrasing, one clear
ask. Keep it short and human. Downstream gates will judge the copy, so write it
as if it ships, because it does.

**An empty pool must still write the file, as `[]`.** If the file is absent the
producer's "no_candidate" could mean "nobody qualified" or "the feeder never ran",
and those must never be the same observable.

CLI: `--inbox <path>` (default episodes/CANDIDATE-INBOX.json), `--ledger <path>`
(default episodes/FUNNEL-LEDGER.json), `--out <path>` (required),
`--now <YYYY-MM-DD>` (default today), `--limit <int>` (default 5).

HOW TO RUN THE CHECK — exactly what the validator runs:

    PYTHONDONTWRITEBYTECODE=1 python3 {CHECK} --repo "$PWD" --u9-only \\
        --out /tmp/u9-selfcheck

It drives your script with a fixture inbox containing a referral, a sourced-list
contact, a person on hold, and a person touched yesterday, and requires: at least
one warm candidate, no cold-to-email, the held person absent, the freshly touched
person absent, the full declared shape on every entry, and `[]` for an empty pool.

OUTPUT CONTRACT: your new file, uncommitted, plus ./fix-summary.md with
`# Fix Summary`, `## Summary`, `## Files Changed`, `## Verification`,
`## Assumptions`, under 900 words, pasting the real final check output under
Verification. Do not weaken or edit the check."""


LANES = [
    ("u8", "revalidator", "codex", 2400, ["scripts/source_truth_revalidate.py"], "u8"),
    ("u9", "feeder", "codex", 2400, ["scripts/guest_candidate_feed.py"], "u9"),
]

tasks = []
for unit, suffix, engine, timeout, owned, mode in LANES:
    key = f"{ROUND}-{unit}-{suffix}"
    owned_args = " ".join(f"--owned {path}" for path in owned)
    tasks.append({
        "key": key,
        "engine": engine,
        "task_type": "code-feature",
        "timeout_s": timeout,
        "spec": u8_spec() if unit == "u8" else u9_spec(),
        "check": (
            f"PYTHONDONTWRITEBYTECODE=1 python3 {WRAPPER} --worktree \"$PWD\" --mode {mode} "
            f"{owned_args} --patch {WORKDIR}/{key}.patch "
            f"--summary fix-summary.md --exported-summary {WORKDIR}/{key}.summary.md"
        ),
        "expect_files": [f"{WORKDIR}/{key}.patch", f"{WORKDIR}/{key}.summary.md"],
        "verified": (
            "the real freshness gate accepts a receipt written seconds ago and blocks simulated "
            "drift, and glob or multi-path refs never fabricate a permanent block"
            if unit == "u8" else
            "warm candidates are produced from referral and inbound sources, cold never reaches "
            "email, held and recently-touched people are excluded, and an empty pool writes []"
        ),
    })

manifest = {
    "run_name": "loop-drive-contract",
    "workdir": WORKDIR,
    "repo": PODCAST,
    "worktrees": True,
    "max_parallel": len(tasks),
    "tasks": tasks,
}

out = HERE / "manifest-r13-inputs.json"
out.write_text(json.dumps(manifest, indent=2) + "\n")
print(f"wrote {out}")
for task in manifest["tasks"]:
    print(f"  {task['key']:<20} {task['engine']:<8} timeout={task['timeout_s']} spec={len(task['spec'])} chars")
