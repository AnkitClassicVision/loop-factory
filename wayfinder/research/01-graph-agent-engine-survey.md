# Survey: graph_agent as Loop Factory v2's map store

Read-only survey of `/mnt/d_drive/repos/graph_agent` (private repo, separate from
loop-factory). Answers Wayfinder ticket `01-graph-agent-engine-survey.md`.

## 1. What it provides today (adopt, don't rebuild)

All of this lives in the `graphagent/` package (1,985 lines total, stdlib-only,
zero pip dependencies — `pyproject.toml:9` `dependencies = []`). Every claim
below is backed by the file:line where the behavior actually lives, not the
README's description of it.

**Single-source invariants (write-time trigger + linter parity).**
`graphagent/rules.py` defines every structural rule exactly once — `COUNT_RULES`
(rules.py:35-87), `PREDICATE_RULES` (rules.py:90-124), `LINT_RULES`
(rules.py:128-174) — and renders each into both a SQLite `BEFORE INSERT`/`BEFORE
UPDATE` trigger (`_render_count_trigger` rules.py:206-219, `_render_predicate_trigger`
rules.py:222-233) and the linter's validator query (`validator_rules`
rules.py:284-316). `schema.build()` executes `rules.render_triggers()` at DB
creation (`schema.py:46`), and `upgrade_v1()` re-executes the same trigger
statements one at a time for legacy DBs (`schema.py:160-161`). Because both the
live gate and the validator are generated from the same dict, they cannot drift
apart — this fixes a named prior bug where the old engine's triggers were
INSERT-only and every UPDATE bypassed them (rules.py:8-9, README.md:42-43).

**Typed node schema.** `node` table carries AAC v1.1 governance fields:
`node_type` (12 work types — `Capture, Validate, Transform, Classify, Decide,
Generate, Lookup, Score, Compose, Execute, Log, Refuse` — plus 3 sinks
`happy_sink/refuse_sink/hard_refuse_sink`, enumerated at `runner.py:30-33`),
`runtime_mode` (D/C/A/H, `SPEC.md:73-75`), `max_lane` ladder
(`read_only < count_only < draft_only < write < send`, ranked at
`runner.py:27`), `action_consequence`, `decision_class`, `confidence_floor`,
`spec_status`, `needs_review`. Schema migrations: `graphagent/schema.py`
(`_NODE_ADDS` schema.py:83-89).

**Typed edges.** `edge` table requires `why` and `how` on every row, enforced
by both trigger and lint (`edge_why_required` / `edge_how_required`,
rules.py:92-110); relationship types are seeded (20 per SPEC.md:16, seed file
`003_seed_rel_types.sql` referenced at `schema.py:18`).

**Refuse-reachability BFS.** `graphagent/validate.py:17-27` (`_reachable_to`) is
a plain BFS from a set of start nodes to a set of target (sink) node ids.
`validate.py:58-67` calls it for every `runtime_mode == "C"` node against the
union of `refuse_sink` and `hard_refuse_sink` node ids, and raises a hard error
(`errors.append(...)`, gates `ok`) if a closed-loop node has no path to any
refuse sink. `validate.py:51-56` also hard-requires a `happy_sink` and
`refuse_sink` to exist at all (warns, doesn't hard-fail, on missing
`hard_refuse_sink`).

**Run cards (PHI-safe telemetry).** `graphagent/runcard.py` has no free-text
payload columns by schema design (`_FIELDS` runcard.py:17-20 has `input_hash`
and `output_ref`, never raw text) and additionally *enforces* the shape at the
Python layer: `write_run_card` rejects any `input_hash` that isn't a 64-char
sha256 hex digest (runcard.py:35-40) and rejects any `output_ref` containing
whitespace or over 512 chars (runcard.py:41-51, `_OUTPUT_REF_MAX`). The runner
writes a run card for every node **and commits it before advancing**
(`runner.py:139-148`, comment at runner.py:9-10) — telemetry is a structural
side effect of running, not a step a model can skip.

**Node health scoring.** `graphagent/score.py` rolls `run_cards` up via a SQL
view `v_node_scores` into a 0-1 `node_health` (score.py:25-36) with
version-controlled weights (`WEIGHTS_VERSION = "v1"`, score.py:16-17) that
weight the *objective validator outcome* (0.45) above self-reported confidence
(0.15) — score.py:17 comment: "a confident hallucinator cannot score its own
node green." `score()` returns a `weak` list of nodes below a floor
(score.py:39-57, default floor 0.6).

**Telemetry drift.** `graphagent/drift.py:_split_rates` (drift.py:22-36) splits
each node's run cards into an older baseline half and a recent half, and
`drift_scan` (drift.py:39-64) raises a `drift_alert` when the recent
validator-fail-rate rises or mean-confidence falls beyond a threshold (default
0.2, drift.py:18). `champion_challenger` (drift.py:92-113) compares
`model_version`s on the same node for A/B judging.

**Runtime gates + STOP-downgrade.** `runner.run()` (runner.py:85-170) walks
trunk order (`_ordered_steps`, runner.py:54-72, computed from `is_trunk` edges
and `exec_order`, never by parsing labels) and enforces, in order: kill switch
(runner.py:115-117) → caller-reported error (118-121) → model self-refusal
(123-124) → lane ≤ max_lane (125-127) → confidence ≥ floor (128-130) →
STOP-downgrade for high-risk C-node writes (131-137). Default policy
`auto_downgrade` holds the action as `pending_approval` at `draft_only`
(runner.py:131-133) rather than hard-blocking; `approve()` (runner.py:173-189)
releases it. `--stop-policy hard_fail` is the alternative (cli.py:255).

**The CLI is the whole API.** `graphagent/cli.py` implements 21 verbs
(`new, add-node, add-edge, govern, spec, rename, retire, move, apply, lint,
validate, export, import, run, approve, score, drift, champion, upgrade,
doctor, rules`), each printing one JSON envelope `{ok, action, ...}` and
exiting 0/1 (cli.py:21-23), so any caller — any model, not just Claude — can
drive it by shelling out and parsing JSON. `export` refuses to produce an
artifact unless `lint` and (for procedural maps) `validate` both pass, unless
`--force` is passed (cli.py:136-152).

**Portability.** Confirmed zero-dependency: `pyproject.toml:9` `dependencies =
[]`; README.md:37 documents `bash tests/smoke_test.sh` runs the whole loop
under `python3 -S` (no site-packages). Ran the smoke test live to confirm it
still passes (see Verification below).

## 2. What is documented but missing

**The `rebuild` verb.** `SPEC.md:49` states: "The committed source is
`knowledge/oplog.jsonl` ... `rebuild` replays them deterministically" — framed
as a CLI verb alongside `new`, `lint`, `export`, etc. (SPEC.md:118-131 lists the
full verb surface and `rebuild` is absent from that list too, which is the
tell). The actual implementation is `graphagent/mutate.py:249-263`, a plain
Python function `replay(db_path)` that reads `oplog.jsonl` and re-applies each
op — but it is **never wired into `cli.py`**. Grep confirms: `cli.py` has no
`cmd_rebuild`, no `sub.add_parser("rebuild")`, and no reference to
`mutate.replay` at all (`cli.py:17` imports `mutate` but only calls
`mutate.add_node/add_edge/set_governance/set_spec/rename_node/retire_node/move_node`
via `cmd_*` wrappers). So today, deterministic rebuild-from-oplog is a private
library function with no CLI surface — a caller cannot invoke `python -m
graphagent rebuild` as SPEC.md implies they can.

**CONTROL_STATE.json for non-dual maps.** `cmd_new` only writes
`CONTROL_STATE.json` when `--dual` is passed (cli.py:57-72) — it is created at
the package root with `topic`, `concept_map`, `procedural_map`, `governance`,
and `flags` keys (cli.py:65-72). For a plain single-map `new` call (the
`else` branch, cli.py:76-80), no `CONTROL_STATE.json` is created anywhere.
Meanwhile `drift.drift_scan()` only writes alerts into a control-state file
if the caller passes `--control-state <path>` explicitly (cli.py:259,
drift.py:39-40, drift.py:62-63 `if alerts and control_state_path`) — and
`_write_flags` (drift.py:76-89) will happily read/write whatever path is
handed to it, creating a fresh `{}`-seeded file if it doesn't exist
(drift.py:78-82). So for a single (non-dual) map, `CONTROL_STATE.json` never
gets created by `new`, and nothing in the CLI defaults or auto-creates one —
the deviation protocol's flag surface (SPEC.md:111-113) has no home unless
the caller manually invents a path and remembers to pass `--control-state`
every time. This is a real gap, not just an omission in the docs: single-map
procedural agents (the common case for loop-factory departments, which don't
build a paired concept map per SPEC.md:31) get no CONTROL_STATE.json at all
by default.

**Other gaps found beyond the ticket's two named items:**
- `cmd_run` (cli.py:160-166) requires a caller to pre-compute the entire
  `results.json` (confidence, proposed_lane, validator_outcome, etc. per node)
  and hand it to `run` in one shot — there's no incremental/interactive
  step-by-step run mode where a model produces one node's result, gets the
  gate verdict, then produces the next. Fine for replay/simulation, awkward
  for a live agent loop.
- No test coverage for the `upgrade_v1` path's interaction with `rules.py`
  trigger regeneration beyond what's in `tests/test_engine.py` (352 lines
  total) — I did not exhaustively verify every migration edge case; this is a
  gap in my own confidence, not a confirmed defect.
- `graphagent/export.py` (139 lines) does Mermaid export and JSON
  import/export but I did not deep-read its escaping logic beyond confirming
  the README's claim (README.md:45) that label-escaping was a fixed bug; take
  that claim as documented, not independently re-verified against a crafted
  adversarial label in this pass.

## 3. Strip-before-reuse checklist (owner/domain coupling)

Grepped the full non-`.git` tree for `Ankit` and `mybcat`/`MyBCAT`. The
coupling is narrow — confined to two markdown pointer files and test/demo
fixtures — and does **not** touch the reusable engine logic
(`rules.py`, `schema.py`, `mutate.py`, `validate.py`, `drift.py`, `score.py`,
`runcard.py`, `cli.py` all came back clean).

| File | Lines | What's there | Strip action |
|---|---|---|---|
| `AGENTS.md` | 1-76 | Full "MyBCAT Universal Rules (Lean v3)" block between `<!-- MYBCAT-GUIDELINES-START/END -->` markers — HIPAA, AWS account specifics, `MyBCAT Playbook MCP`, `secret-store` CLI, a hardcoded path `/mnt/d_drive/repos/context_nate/outputs/operating-model-reference.md` | Delete lines 1-76 entirely (auto-synced by `mybcat-sync-guidelines`, not hand-authored — do not try to hand-edit, just drop the block). Keep lines 78-94 (the generic engine pointer). |
| `GEMINI.md` | 1-76 | Identical MyBCAT block, same markers | Same as above; keep lines 78-90. |
| `SPEC.md` | 121, 130 | `--set owner=Ankit`, `--who Ankit` in example CLI invocations | Replace with a placeholder like `--set owner=<name>` / `--who <name>` |
| `graphagent/runner.py` | 12 | Docstring comment: `STOP-downgrade policy = "auto-downgrade to approval" (Ankit, 2026-05-31)` | Cosmetic; either keep as a design-decision attribution (it's a comment, not data) or genericize to "(design decision, 2026-05-31)" |
| `tests/test_engine.py` | 130, 147 | `owner="Ankit"` fixture, `runner.approve(db, card, who="Ankit")` | Rename to a generic test fixture name (`"test-owner"` or similar) — zero functional impact |
| `tests/smoke_test.sh` | 18, 53 | `--set owner=Ankit`, `--who Ankit`; demo scenario name `"After-Hours Intake"` and string `"patient says clinic was closed"` | Rename owner to a generic placeholder; the "After-Hours Intake" / patient-facing demo scenario is optometry/healthcare-flavored (this repo's author builds for an optometry back-office) — swap for a domain-neutral demo (e.g. a generic support-ticket triage) so the smoke test doesn't read as tied to one vertical |
| `skill/SKILL.md` | — | Clean. Uses the same "After-Hours Intake" example (lines ~25-40) but no owner/org strings | Optional: swap the example scenario alongside the smoke test for consistency, not required |
| `.agents/skills/token-saver/` | — | Present on disk but **not git-tracked** (`git ls-files .agents` returns nothing) | Not part of the reusable repo content; ignore — it won't come along in a clone/export |
| `resume.md` | whole file | Codex session-resume metadata (branch, HEAD sha, session transcript path) | Not part of the engine; exclude from any adoption copy (it's operational scratch, already the kind of file that shouldn't ship) |
| `CLAUDE.md` | whole file | Already generic — no owner/org coupling found | No action needed |

**Net assessment:** the coupling is cosmetic and localized to 2 markdown files
(auto-synced governance boilerplate, mechanically strippable) plus a handful
of test/demo strings. None of it is load-bearing in the engine's Python
modules. A strip pass is a same-day mechanical edit, not a redesign.

## Verification

Ran the smoke test live (read-only against the repo, writes only to a
`mktemp` dir, no changes to graph_agent itself):

```
$ bash tests/smoke_test.sh
== new (procedural) ==
== build the graph via verbs (no SQL) ==
== lint + validate (must pass) ==
== export (refuses unless draftable) ==
== run (Draft Reply must auto-downgrade to approval) ==
   pending approval -> rc_2ce18f54e3b1
== approve the held action ==
== score (telemetry -> node_health) ==
   scored nodes with runs: 3
== doctor ==
SMOKE OK
```

This confirms lint/validate/export/run/approve/score/doctor all work
end-to-end as documented, on this checkout, right now.

## Adopt / Strip / Gap checklist (for the ticket-12 decision session)

| # | Item | Category | Evidence | Action needed before adoption |
|---|---|---|---|---|
| 1 | Trigger+linter parity engine (`rules.py`) | ADOPT | rules.py:35-330 | None — copy as-is |
| 2 | Typed node schema (12 types + 3 sinks, runtime_mode, max_lane) | ADOPT | schema.py, runner.py:27-33 | None |
| 3 | Typed edges (why/how required, 20 rel types) | ADOPT | rules.py:92-124 | None |
| 4 | Refuse-reachability BFS | ADOPT | validate.py:17-27, 58-67 | None |
| 5 | PHI-safe run cards (hash-only, no payload columns) | ADOPT | runcard.py:15-51 | None — this is stricter than loop-factory's current sanitized-digest convention; worth diffing against `factory/memory.py`'s approach before merging the two |
| 6 | Node health scoring (weighted, version-controlled) | ADOPT | score.py:16-36 | None |
| 7 | Telemetry drift + champion/challenger | ADOPT | drift.py:22-113 | None |
| 8 | STOP-downgrade runtime gate | ADOPT | runner.py:85-170 | None |
| 9 | CLI JSON-envelope contract | ADOPT | cli.py:21-23, 21 verbs | None |
| 10 | `rebuild` CLI verb | GAP — build it | mutate.py:249-263 (function exists), cli.py (no wiring) | Add `cmd_rebuild` + `sub.add_parser("rebuild")` calling `mutate.replay()`; trivial, ~10 lines |
| 11 | CONTROL_STATE.json for non-dual maps | GAP — build it | cli.py:57-80 (only written on `--dual`) | Either always write a minimal CONTROL_STATE.json in the non-dual `new` path, or make `drift`/`score` default `--control-state` to a package-relative path when unset |
| 12 | MyBCAT guideline blocks in AGENTS.md / GEMINI.md | STRIP | AGENTS.md:1-76, GEMINI.md:1-76 | Delete both blocks before any external/portable reuse |
| 13 | `owner=Ankit` / `who=Ankit` in SPEC/tests/smoke demo | STRIP | SPEC.md:121,130; test_engine.py:130,147; smoke_test.sh:18,53 | Replace with generic placeholder strings |
| 14 | "After-Hours Intake" / patient demo scenario | STRIP (optional but recommended) | smoke_test.sh:8-53, skill/SKILL.md ~25-40 | Swap to a vertical-neutral demo scenario for a genuinely portable engine story |
| 15 | Incremental/interactive `run` mode | GAP — not built, not requested by ticket, flagging for awareness | runner.py:85 (`run` takes a complete `results` dict up front) | Out of scope for adoption decision; note as a future limitation if loop-factory wants live step-by-step gating rather than replay |
