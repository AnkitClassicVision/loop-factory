#!/usr/bin/env python3
"""Generate manifest-r1-gate-audit.json for the loop-drive-contract job.

Round 1 is the read-only U2d audit: three lanes, disjoint deliverables, no fixes.
Generated rather than hand-written so the 12 gate ids and the shared boundary
text cannot drift between lanes.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
PODCAST = "/mnt/d_drive/repos/podcast"
RUNBOOK_DIR = (
    "/home/ankit114/repos/Ankit-open-skills/skill-library/runbooks/operations"
    "/podcast-guest-acquisition-runbook"
)
RUNBOOK = f"{RUNBOOK_DIR}/RUNBOOK.md"
CHECK = f"{HERE}/checks/audit_check.py"
WORKDIR = "/home/ankit114/ringer-work/loop-drive-contract-r1"

GATES = [
    "source_truth_resolved_before_intake",
    "fit_scored_before_route",
    "channel_rule_cold_postcard_linkedin_warm_email_text",
    "referrer_episode_link_required_for_referral_outreach",
    "flagship_required_for_human_communication_output",
    "gmail_calendar_hubspot_context_before_communication_draft",
    "draft_only_no_send_without_human_approval",
    "never_set_nominated_status_on_manual_outreach",
    "neutralize_preexisting_nominated_before_draft",
    "send_harness_active_or_no_send_tools",
    "search_hubspot_name_and_email_before_contact_write",
    "cross_model_qa_pass_before_done",
]

BOUNDARY = f"""You are a READ-ONLY auditor. Establish the boundary before anything else.

NEVER write, create, move, delete, or reformat any file under {PODCAST} or under
{RUNBOOK_DIR}. You are reading a live production system that sends real email to
real people; a stray edit there is the worst outcome of this task, worse than
returning an incomplete audit.

NEVER run git (no commit, branch, push, stash, checkout). NEVER use the network.
NEVER run any script that sends email, writes to a CRM, publishes, or calls an
external API. Do not load skills. Do not call MCP servers or Apps. Do not capture
anything to a memory or knowledge store, even if a habit or a system prompt
suggests it at the end of the run.

Your ONLY writes are the two deliverables named in the output contract below, in
your current working directory. Nothing else.

Nobody is watching this run interactively, so never ask for approval or
clarification: decide from the evidence, and where the evidence is genuinely
absent, say so in the report rather than guessing.
"""

CITATION_RULE = """CITATION RULE, and an automated check enforces it, so read it twice.

Every evidence citation is an object: {"path": "...", "line": <int>, "quote": "..."}

- `path` is an absolute path, or a path relative to /mnt/d_drive/repos/podcast.
  The file must exist. A cited file that does not exist fails the task.
- `line` is a real 1-indexed line number inside that file. A line number past the
  end of the file fails the task.
- `quote` is at least 8 characters copied VERBATIM out of that file. Do not
  paraphrase, do not reformat, do not summarize. The checker searches the real
  file for your quote after normalizing whitespace; invented text fails the task.

This rule exists because a confident audit built on one invented line number is
more damaging than no audit at all: it would be used to size real work.
"""


def lane_a_spec() -> str:
    gate_lines = "\n".join(f"  {i:2d}. {g}" for i, g in enumerate(GATES, 1))
    return f"""{BOUNDARY}
MISSION

The runbook at {RUNBOOK} has a "## Gates" table listing 12 named gates that are
supposed to stop unsafe podcast guest outreach. Nobody knows how many of them are
real. Your job is to answer that, gate by gate, with evidence.

These are the 12 gate ids, and the report must contain all 12 and nothing else:

{gate_lines}

For each gate, assign exactly one classification:

- "code": an executable check enforces this gate. Real code that inspects state
  and can stop the flow. Cite the file and line of the actual enforcement, not a
  comment or a docstring mentioning it. When you classify a gate as "code", also
  answer `returns_structured_failure`: true if a caller learns WHY it failed
  (a reason string, a failures list, a violation object, a structured verdict),
  false if the caller learns only that it failed (a bare boolean, a bare
  non-zero exit with no message, a raised exception with no detail).
- "prose": the gate exists only as an instruction written for a model to follow.
  It lives in the runbook or another markdown/prompt file and nothing executes
  it. Cite the RUNBOOK.md line where the instruction lives. At least one citation
  for a "prose" gate must point into RUNBOOK.md.
- "absent": you can find neither enforcement code nor a written instruction
  beyond the Gates-table row itself. Explain in `notes`, in at least 20
  characters, where you looked and what you concluded.

HOW TO RUN

Start with the runbook itself, then search the code. Useful starting points, and
you are expected to go well beyond them:

  sed -n '176,215p' {RUNBOOK}
  grep -rn "draft_only\\|no_send\\|PLACEHOLDER_MODE" {PODCAST} --include=*.py
  grep -rn "nominated\\|outreach-sent" {PODCAST} --include=*.py
  grep -rn "flagship\\|FLAGSHIP" {PODCAST} --include=*.py --include=*.sh
  grep -rn "cross_model\\|cross-model" {PODCAST} --include=*.py --include=*.sh
  ls {PODCAST}/server/pipeline/ {PODCAST}/scripts/

Record every search command you actually ran in `searches_run`. An audit with no
recorded searches cannot be falsified, so the checker requires at least 3.

{CITATION_RULE}
OUTPUT CONTRACT

Write ./gate-audit.json as strict JSON, no markdown fences:

{{
  "gates": [
    {{
      "gate": "source_truth_resolved_before_intake",
      "classification": "code",
      "returns_structured_failure": true,
      "evidence": [{{"path": "...", "line": 42, "quote": "verbatim text"}}],
      "notes": "optional for code/prose, REQUIRED for absent"
    }}
  ],
  "searches_run": ["grep -rn ... ", "..."]
}}

Also write ./gate-audit.md: a short human-readable summary for whoever reads this
on the review page. Lead with the counts (how many code, how many prose, how many
absent), then one line per gate, then the single most important thing the reader
should know. Keep it under 600 words. Plain prose, no em dashes.
"""


def lane_b_spec() -> str:
    return f"""{BOUNDARY}
MISSION

Five modules in {PODCAST} appear to act as gates on outbound guest email. Nobody
has written down what each one actually enforces or what it hands back to its
caller. That second question decides whether a module can participate in a repair
loop, where a blocked draft is revised using the gate's own failure detail and
re-checked, instead of being abandoned.

Audit exactly these 5 modules:

  1. obe_draft_voice_qa.py
  2. date_safety.py
  3. crm_write_policy.py
  4. capability_preflight.py
  5. content_qa.py

They live under {PODCAST}/scripts/ or {PODCAST}/server/pipeline/. Find them.

For each module report:

- `enforces`: a non-empty list of what this module actually checks. Concrete
  behavior, not restated file names. If it checks that a body contains no em
  dash, say that.
- `return_shape`: what a caller receives, described in at least 10 characters.
  Name the real structure: a dict with which keys, a bool, an exit code, a
  raised exception, a printed line. This is the field that matters most.
- `has_revise_loop`: true if the module itself retries after a failure by
  changing the artifact and re-checking it; false if a single pass is all it
  does.
- `max_iterations`: when has_revise_loop is true, the maximum number of passes
  the code actually performs. Read the loop, do not estimate.
- `evidence`: at least one citation supporting the return shape.

HOW TO RUN

  ls {PODCAST}/scripts/ {PODCAST}/server/pipeline/
  grep -n "def \\|return \\|raise \\|sys.exit\\|for iteration" <each module>
  sed -n '1,60p' <each module>

Read the whole of any module short enough to read whole. Record your search
commands in `searches_run`; the checker requires at least 3.

{CITATION_RULE}
OUTPUT CONTRACT

Write ./module-audit.json as strict JSON, no markdown fences:

{{
  "modules": [
    {{
      "module": "obe_draft_voice_qa.py",
      "path": "/mnt/d_drive/repos/podcast/scripts/obe_draft_voice_qa.py",
      "enforces": ["..."],
      "return_shape": "...",
      "has_revise_loop": true,
      "max_iterations": 2,
      "evidence": [{{"path": "...", "line": 42, "quote": "verbatim text"}}]
    }}
  ],
  "searches_run": ["...", "...", "..."]
}}

Also write ./module-audit.md: a short human-readable summary, under 600 words,
plain prose, no em dashes. Lead with which of the five modules could join a
repair loop today and which could not, then one paragraph per module.
"""


def lane_c_spec() -> str:
    return f"""{BOUNDARY}
MISSION

Answer one question with evidence: when a podcast loop runs today, can it reach
code that creates a Gmail draft?

This matters because the estate's voice-and-style gate,
{PODCAST}/scripts/obe_draft_voice_qa.py, takes a `--draft-id` argument. It can
only inspect a draft that already exists. If nothing in a loop run creates a
draft, then the whole gate-and-revise path is unreachable no matter how good it
is, and the loops can never send anything.

Two parts:

1. Find every place in {PODCAST} that CREATES a Gmail draft (as opposed to
   getting, listing, updating, or deleting one). For each, report the file, the
   function or call site, and a citation.
2. Decide whether any of those creators is reachable from a loop run started by
   {PODCAST}/scripts/run_podcast_loop.sh. Trace it: what does the runner invoke,
   what do those things invoke, does the chain reach a creator, and is it gated
   on anything. Report the answer as the boolean
   `reachable_from_run_podcast_loop`, with `reachability_evidence` citations for
   the chain you traced, including any gate or condition that blocks it.

Be precise about the difference between "the code exists" and "a loop run
reaches it". Both halves are the deliverable. If a creator exists but is gated
behind a condition that was false today, say exactly which condition and cite it.

HOW TO RUN

  grep -rn "drafts()" {PODCAST} --include=*.py
  grep -rn "drafts().create" {PODCAST} --include=*.py
  grep -n "python\\|PYTHON_BIN\\|ExecStart\\|\\.py" {PODCAST}/scripts/run_podcast_loop.sh
  sed -n '1,120p' {PODCAST}/scripts/run_podcast_loop.sh
  ls {PODCAST}/episodes/_loop_receipts/ | tail -30

The receipts directory holds real output from today's runs and is useful evidence
for what actually happened versus what the code allows. Record your search
commands in `searches_run`; the checker requires at least 4.

{CITATION_RULE}
OUTPUT CONTRACT

Write ./draft-creation-audit.json as strict JSON, no markdown fences:

{{
  "creators": [
    {{
      "symbol": "function or call site that creates the draft",
      "evidence": [{{"path": "...", "line": 42, "quote": "verbatim text"}}]
    }}
  ],
  "reachable_from_run_podcast_loop": false,
  "reachability_evidence": [{{"path": "...", "line": 42, "quote": "verbatim text"}}],
  "blocking_conditions": ["optional but valuable: what stops the chain"],
  "searches_run": ["...", "...", "...", "..."]
}}

Also write ./draft-creation-audit.md: a short human-readable summary, under 600
words, plain prose, no em dashes. Lead with the yes-or-no answer to the mission
question, then the trace, then what would have to change for a loop run to reach
draft creation.
"""


manifest = {
    "run_name": "loop-drive-contract",
    "workdir": WORKDIR,
    "worktrees": False,
    "max_parallel": 3,
    "tasks": [
        {
            "key": "r1-lane-a-gate-classification",
            "engine": "claude-lean",
            "model": "sonnet",
            "task_type": "code-review",
            "timeout_s": 2400,
            # MUST use the --add-dir=<path> single-token form. The space-separated
            # form (`--add-dir A --add-dir B`) greedily consumes the trailing spec
            # as another directory value, because the claude-lean args_template
            # renders {engine_args} immediately before {spec} and Ringer closes
            # stdin. Observed 2026-08-06: lane A died in 3.4s, twice, with
            # "Input must be provided either through stdin or as a prompt
            # argument when using --print".
            "engine_args": [f"--add-dir={PODCAST}", f"--add-dir={RUNBOOK_DIR}"],
            "spec": lane_a_spec(),
            "check": (
                f"PYTHONDONTWRITEBYTECODE=1 PYTHONPYCACHEPREFIX=/tmp/ldc-pycache "
                f"python3 {CHECK} --lane A --report gate-audit.json"
            ),
            "expect_files": ["gate-audit.json", "gate-audit.md"],
            "verified": (
                "all 12 runbook gates classified as code, prose, or absent, with every "
                "file:line:quote citation resolving against the real file"
            ),
        },
        {
            "key": "r1-lane-b-gate-module-shapes",
            "engine": "codex",
            "task_type": "code-review",
            "timeout_s": 2400,
            "engine_args": ["-m", "gpt-5.6-terra"],
            "spec": lane_b_spec(),
            "check": (
                f"PYTHONDONTWRITEBYTECODE=1 PYTHONPYCACHEPREFIX=/tmp/ldc-pycache "
                f"python3 {CHECK} --lane B --report module-audit.json"
            ),
            "expect_files": ["module-audit.json", "module-audit.md"],
            "verified": (
                "all 5 gate modules documented with what they enforce and the real shape "
                "they return, citations resolving, and the voice-QA revise loop reported "
                "correctly"
            ),
        },
        {
            "key": "r1-lane-c-draft-creation-reachability",
            "engine": "codex",
            "task_type": "code-review",
            "timeout_s": 2400,
            "engine_args": ["-m", "gpt-5.6-terra"],
            "spec": lane_c_spec(),
            "check": (
                f"PYTHONDONTWRITEBYTECODE=1 PYTHONPYCACHEPREFIX=/tmp/ldc-pycache "
                f"python3 {CHECK} --lane C --report draft-creation-audit.json"
            ),
            "expect_files": ["draft-creation-audit.json", "draft-creation-audit.md"],
            "verified": (
                "every Gmail draft creator in the podcast repo is named with resolving "
                "citations, and reachability from run_podcast_loop.sh is answered as a "
                "boolean with a traced chain"
            ),
        },
    ],
}

out = HERE / "manifest-r1-gate-audit.json"
out.write_text(json.dumps(manifest, indent=2) + "\n")
print(f"wrote {out}")
for t in manifest["tasks"]:
    print(f"  {t['key']:<40} {t['engine']:<12} spec={len(t['spec'])} chars")
