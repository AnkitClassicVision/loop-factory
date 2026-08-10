#!/usr/bin/env python3
"""Generate manifest-r1-terminology.json.

Ankit's challenge, 2026-08-10: the coordinator coined "hollow automation" for a
failure mode and he asked whether that is real vocabulary or invented, since a
future person or AI would not recognise a made-up term. This round asks three
lanes with different domain memories to name the mode from ESTABLISHED
literature, and to say plainly where no established term exists.

Model diversity is the point of the round, so the lanes deliberately differ.
GLM runs through the OpenRouter API lane: Ankit named it explicitly (twice) as a
Fable replacement, and this is a read-only research task with no repo writes, so
the spend is named rather than silent.

Edit LANES, then:  python3 build_r1.py
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
CHECK = f"{HERE}/checks/terminology_check.py"
WORKDIR = "/mnt/d_drive/ringer-work/failure-mode-terminology-r1"

INCIDENT = """THE INCIDENT, described in neutral terms so you are not led toward a
conclusion. A headless automation was built to draft one outreach email per run
and hand it to an existing delivery pipeline. Two components were written
separately: a producer module, and the runner that invokes it. Each was verified
by its own executed tests, and both passed.

In production the runner invoked the producer with two arguments; the producer
required a third. The argument parser rejected the call and exited with status
2 BEFORE the producer wrote any output file. The runner interpreted status 2 as
the producer's own legitimate "no eligible candidate today" result — the same
status code the producer returns for that benign case — and logged "stopped
cleanly". The automation therefore reported a healthy no-op on every single run
while being structurally incapable of ever doing its job. It also fails closed:
no email is created, so nothing harmful is sent, and the silence looks like
normal quiet operation.

Three properties of the incident, which may or may not each have their own
established name:
  (a) Every component was verified; the JOINT between components never was. No
      test ever executed the runner calling the producer for real.
  (b) The error signal was observably identical to a legitimate benign outcome
      (same exit status, similar log line), so no monitor could distinguish
      "broken" from "nothing to do".
  (c) The system reported success while producing nothing, indefinitely, and the
      quietness of a fail-closed design made the absence look like health."""

CONTRACT = f"""{INCIDENT}

YOUR JOB. Identify what ESTABLISHED, PRE-EXISTING vocabulary the engineering,
research and operations literature already uses for this mode and its three
properties. The owner's explicit concern is that a coined phrase is worthless
because a future person or AI will not recognise it. Prefer terms with real
provenance — a named field, paper, book, standard, well-known practice or
widely used tool — over anything you would have to invent.

HARD HONESTY RULE. If a property has no established name, say so in the required
"No established term" section rather than inventing one. Inventing vocabulary is
the failure being studied; do not reproduce it. Where you are unsure whether a
term is genuinely established versus your own paraphrase, mark it UNCERTAIN and
say why. A shorter honest report beats a longer confident one.

OUTPUT FORMAT, enforced by an executed validator. Write ONE markdown file at
{WORKDIR}/{{report}}, structured as:

  # <your lane name>

  ## Established terms

  ### <Term>
  - Definition: <one or two sentences, in your own words>
  - Provenance: <field, and where it comes from — paper, book, standard,
    community practice, tool. Name people or documents when you can. If you are
    recalling rather than certain, write UNCERTAIN and explain.>
  - Fit: <does it describe the whole incident, only property (a), (b) or (c), or
    something adjacent? Say plainly where it does NOT fit — a term that only
    half fits is more useful when its edges are named.>

  (Repeat for at least SIX terms. More is better if each earns its place.)

  ## Mapping
  A short table or list assigning the best term to the whole mode and to each of
  properties (a), (b), (c). If two terms compete for the same slot, say which you
  would put in a runbook and why.

  ## Detection
  How someone would NOTICE this in the wild, ordered cheapest first. Prefer
  established practice names over descriptions where they exist.

  ## Design rules
  How to build so it cannot happen. Again, name the established practice where
  one exists rather than describing it generically.

  ## No established term
  What you could NOT find real vocabulary for, stated plainly. If everything
  mapped cleanly, say that and explain why you are confident.

BOUNDARY. Read-only research and writing. Write nothing except your one report
file. Do not modify any repository. Do not run git. Do not load skills. Do not
call MCP tools or Apps. You have no network access assumption — work from your
own knowledge and mark uncertainty honestly rather than fabricating citations.
Do not invent paper titles, author names, page numbers or URLs; if you cannot
attribute precisely, describe the provenance in general terms and mark it
UNCERTAIN."""

LANES = [
    ("sre", "codex", None,
     """LANE LENS: distributed systems, site reliability engineering and
observability. Think about how this mode is discussed by people who run large
systems: partial and non-crash failures, health signals that disagree with user
experience, monitoring that only watches for errors, liveness versus safety
properties, and the practice of alerting on the absence of an expected event."""),
    ("testing", "codex", None,
     """LANE LENS: software testing, formal verification and API/contract design.
Think about how this mode is discussed by people who verify software: tests that
pass without exercising anything, coverage that hides gaps, the balance between
unit and integration testing, verifying the boundary between two independently
built components, and the classic problem of a return value that cannot
distinguish an error from a legitimate result."""),
    ("safety", "glm-openrouter", "openrouter/z-ai/glm-5.2",
     """LANE LENS: safety engineering, human factors and control theory. Think
about how this mode is discussed by people who study accidents and automation in
aviation, medicine and industrial control: operators' mental models diverging
from what the machine is actually doing, protective systems that are inert
without anyone knowing, the difference between a system that stops loudly and
one that stops quietly, and testing whether a safety function still works."""),
]

tasks = []
for name, engine, model, lens in LANES:
    key = f"r1-terms-{name}"
    report = f"{WORKDIR}/{key}.md"
    task: dict[str, object] = {
        "key": key,
        "engine": engine,
        "task_type": "research",
        "timeout_s": 1500,
        "spec": CONTRACT.replace("{report}", f"{key}.md") + f"\n\n{lens}",
        "check": f"python3 {CHECK} --report {report} --min-terms 6",
        "expect_files": [report],
        "verified": ("every term carries a definition, a provenance the reader can judge, and an "
                     "explicit fit-or-not against the incident, plus a named gap section"),
    }
    if model:
        task["model"] = model
    tasks.append(task)

manifest = {
    "run_name": "failure-mode-terminology",
    "workdir": WORKDIR,
    "max_parallel": len(tasks),
    "tasks": tasks,
}

out = HERE / "manifest-r1-terminology.json"
out.write_text(json.dumps(manifest, indent=2) + "\n")
print(f"wrote {out}")
for task in manifest["tasks"]:
    print(f"  {task['key']:<18} {task['engine']:<16} spec={len(task['spec'])} chars")
