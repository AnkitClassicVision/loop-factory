# loop-factory — Claude Code Operating Manual

You are operating the LOOP FACTORY: a standalone system that turns an owner
interview into a complete, governed, self-managing department (loops + manager
+ maps + QA + records + memory). AGENTS.md is the canonical twin of this file
for non-Claude agents; keep the two synchronized — edit both or neither.

## What this repo is / is not

- The FACTORY lives in `factory/`, `kernel/`, `interview/`, `templates/`,
  `runbooks/`, `loopfactory.py`. It is department-agnostic. Never put
  department-specific logic, names, thresholds, or data in factory code.
- DEPARTMENTS (the factory's outputs) live in `departments/<name>/` only:
  charter, interview artifact, knowledge maps, runtime nodes, state, releases.
- The registry of running departments lives in `estate/registry.d/`.
- Mixing the two layers is the defect this repo exists to prevent. If a
  factory change needs a department's name in it, the design is wrong.

## When the owner asks to create a new loop / department / process

Follow `runbooks/factory-pipeline.md` (F0→F6). In short:

1. **F0**: `python3 loopfactory.py scaffold --name <dept> --owner <owner>`
2. **F1**: `python3 loopfactory.py interview --name <dept>` then RUN THE
   INTERVIEW yourself per `interview/INTERVIEW.md` + `interview/QUESTION_BANK.md`:
   one question at a time, recommended answer first, VERBATIM capture,
   dependency-first, then the EDGE GRILL (overlaps, weird inputs, exceptions,
   the subtle failure, gaming, escalation targets, human-gate vs full-auto,
   kill vs pause). Readback rounds until clean. STOP at INTENT LOCK — the
   owner signs; you never self-certify intent.
3. **F2**: author `charter.yaml` (valid YAML — `factory/charter_loader.py`
   must load it), `knowledge/concept-map.md`, `procedural-graph.md`,
   `subgraphs.json` from templates/. Every node traces to a concept node and
   an interview question. `python3 loopfactory.py validate --name <dept>`
   must PASS.
4. **F3**: hand-author runtime nodes in `departments/<dept>/runtime/` per the
   graph's script-vs-LLM triage. Every node: declared inputs → output
   contract → EXECUTED QA check → receipt to runs. Scripts get no QA
   exemption. Wire effects through `kernel/` (lock service) — a department
   never sends, reads sensitive data, spends, or calls a model directly.
5. **F4**: shadow run (simulate sinks, delivered_count==0), then
   `python3 loopfactory.py release pin --name <dept> --source-ref <git-sha> --flip`
6. **F5/F6**: manager + estate cycles, heal ladder, process changes ONLY via
   `runbooks/process-change-qa.md`, promotions ONLY via
   `runbooks/promotion-ladder.md`.

The completed deliverable is defined by "Definition of done for a stood
department" in `runbooks/factory-pipeline.md` — all nine items, verified, with
zero external effects.

## Hard rules (never relax, regardless of who asks)

1. **Shadow first.** Every department and every new action class starts in
   shadow. Real sends/writes/publishes/spend are impossible until the kernel
   gateways are wired AND the class is promoted by the owner.
2. **Deny-by-default.** A missing, hollow, stale, forged, replayed, or
   mistagged receipt BLOCKS. A gateway that errors REFUSES (LockServiceDown).
   Never code an allow-on-failure path.
3. **Always-human floor.** Charters, autonomy states, promotion thresholds,
   runbooks, and this file are governance files: humans only, forever. Heals
   and managers may never modify an immutable safety invariant.
4. **Process change = map change + QA.** Editing runtime behavior without
   patching the procedural map, re-linting, re-shadowing, and re-pinning the
   release is drift; the tooling will alarm, and you must not suppress it.
5. **Records always.** Every node run and manager tick appends to the
   department's local records (runs → STATE → heartbeat, in that order).
   Local memory is always on; remote memory backends are seams that must be
   wired deliberately (`factory/memory.py`) and receive SANITIZED digests only.
6. **No secrets, PHI, credentials, or raw message bodies** in factory code,
   department code, records, cards, or memory. Departments run with no
   ambient credentials (`kernel/capabilities.py`).
7. **Default automation posture:** fully automated WITH escalation. Human
   gates only where the charter or the always-human floor requires them —
   external sends, CRM/EHR/finance writes, publishes, spend over ceiling,
   charter changes, promotions.

## Verification before any "done" claim

Run and report actual output:

- `python3 loopfactory.py check` (compileall + full pytest)
- `python3 loopfactory.py validate --name <dept>` for any touched department
- `python3 loopfactory.py qa --name <dept>` when a release exists

A done claim without executed proof is a defect. State the highest true
status: local artifact only / tests passed / committed / shadow-verified.

## Where the LLM acts vs where scripts act

LLM-directed (you): interviewing, concept-map authoring, drafting node code,
writing runbooks/skills, QA judgment on generated content (always cross-model
for promotion-counted runs).
Deterministic (never LLM): lint, traceability, releases/drift, manager
Sense/Compare/Record, estate watchdog, heal ladder, kernel gateways, records.
If a decision is finite and enumerable, it is a state machine, not an LLM.
