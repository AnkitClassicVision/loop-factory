# loop-factory

A standalone factory that turns an owner interview into a complete, governed,
self-managing **department**: worker loops, a deterministic manager loop, an
estate watchdog, self-healing, content-addressed releases, knowledge maps
(concept + procedural) with QA-enforced change control, local-first records,
and pluggable memory — with a deny-by-default runtime kernel so the wrong
action is impossible, not merely undocumented.

ELI5: you describe a process once, the factory grills you about the edges,
locks your intent, and stands up a department that runs it on rails — fully
automated with escalation, human gates only where they must exist, and every
change forced back through the map and QA.

## Quickstart

```
python3 loopfactory.py check                      # self-test (compileall + pytest)
python3 loopfactory.py scaffold --name demo --owner you
python3 loopfactory.py interview --name demo      # then run the F1 interview (see below)
python3 loopfactory.py validate --name demo       # charter + map QA
python3 loopfactory.py manager --name demo        # one manager cycle (shadow)
python3 loopfactory.py release pin --name demo --source-ref dev --flip
python3 loopfactory.py qa --name demo             # full QA incl. release drift
```

The interview and map authoring are agent-driven: open this repo in Claude
Code / Codex and say "create a new department" — CLAUDE.md / AGENTS.md carry
the full operating manual. Everything else is deterministic CLI.

## Layout

```
factory/     department-agnostic machinery (scaffold, manager, heal, release,
             estate watchdog, human-in-the-loop, charter loader, map QA, memory)
kernel/      deny-by-default runtime enforcement (receipts, gateways, lock service)
interview/   the F1 grill-me interview protocol + question bank
templates/   charter / maps / runbook / skill / systemd templates
runbooks/    factory pipeline (F0→F6), process-change QA, promotion ladder
departments/ the factory's OUTPUTS — one dir per stood department
estate/      registry of running departments (watchdog reads it)
tests/       the factory's own test suite (`loopfactory.py check`)
```

Factory vs department is a hard boundary: factory code never contains a
department's name, thresholds, or data. Departments never contain copies of
factory machinery.

## Safety posture

Shadow-first, deny-by-default, always-human governance files, no ambient
credentials in department processes, no secrets/PHI in code/records/memory,
kill switches per department, and promotions earned per action class with
independent evidence. See AGENTS.md "Hard rules."

## Provenance

Extracted and generalized from the open-engine department-factory work
(design spec 2026-07-21 v2.0 + runtime kernel + sales calibration department).
See docs/PROVENANCE.md for the file-level map.
