# P2: Compiled Clief-Notes Department Surface + Drift CI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every department gets a human-inspectable clief-notes surface (AGENTS.md master, CLAUDE.md pointer, ROUTER.md, numbered workspace folders with CONTEXT.md + references/) that is GENERATED from `subgraphs.json` — humans write prose, never edges — with a drift check inside `validate` that fails when the surface disagrees with the topology.

**Architecture:** Phase P2 of `docs/superpowers/specs/2026-08-05-loop-brain-reconcile-design.md` (C1). One new factory module, `factory/surface_compiler.py`, department-agnostic (hard rule: no department names in factory code). Generated regions live between markers; human prose lives outside them and survives regeneration byte-for-byte. `factory/graphs.py qa()` gains a surface check that runs only for departments that have adopted the surface (adoption = `ROUTER.md` exists), so other departments are untouched. The department's own subgraphs count sets the workspace count (podcast: 6) — the clief-notes four-workspace heuristic governs human-authored workspaces, not compiled machine surfaces; noted, not violated.

**Tech Stack:** Python 3 stdlib, pytest. No new dependencies. No release re-pin needed (no `runtime/` files change; surface files are knowledge-layer).

## Global Constraints

- Factory stays department-agnostic: `surface_compiler.py` must contain no department name, threshold, or path constant beyond `departments/<dept>/` conventions.
- No placeholders in generated output: an empty human region renders as `_No owner notes yet._` (explicit state, not TBD).
- Marker grammar (exact): `<!-- GENERATED:BEGIN section=<name> source=subgraphs.json -->` ... `<!-- GENERATED:END section=<name> -->`. Everything outside marker pairs is human-owned and preserved verbatim on regeneration.
- Deny-by-default in the checker: unreadable subgraphs.json, malformed markers, or a generated region that cannot be parsed → FAIL with the file and reason, never a silent skip.
- Watch every new test fail RED before implementing.

## Frozen interfaces

- `generate(dept_dir: Path) -> list[Path]` — (re)writes the surface, preserving human regions; returns written paths.
- `check_surface(dept_dir: Path) -> list[str]` — [] when clean; else one failure string per file: `"<relpath>: <reason>"`. Returns [] (adopted-check skipped) when `<dept_dir>/ROUTER.md` does not exist.
- CLI: `python3 -m factory.surface_compiler generate --dept-dir <path>` (rc 0; rc 1 on refusal) and `... check --dept-dir <path>` (rc 0 clean, rc 1 fails printed one per line).
- `factory/graphs.py qa()` result dict gains key `"surface": <list of failure strings>` and `"ok"` accounts for it.

Surface layout generated per department:

```
departments/<dept>/
  AGENTS.md      master control: generated routing summary + invariants; human region below
  CLAUDE.md      fully generated 3-line pointer to AGENTS.md
  ROUTER.md      fully generated: task-kind → workspace table + unknown-route STOP rule
  NN_<slug>/     one per subgraph, NN = 2-digit order of appearance, slug = id
                 lowercased minus the SG- prefix, hyphens to underscores
    CONTEXT.md   generated: Purpose (from subgraph id + concept_refs), Node chain
                 (ordered impl list), Inputs (L3: charter.yaml, references/;
                 L4: state/ paths), Outputs, Verify pointer to procedural-graph.md
                 row; human region below the generated block
    references/README.md  generated: half-life rule text ("every reference file
                 carries Last-updated and a half-life; expired references are
                 stale context")
```

### Task 1: `factory/surface_compiler.py` + qa hook + tests (single lane)

**Files:**
- Create: `factory/surface_compiler.py`
- Modify: `factory/graphs.py` (qa() gains the surface key; import surface_compiler by file path exactly the way `loopfactory.py` `_load()`s modules — no package import)
- Test: `tests/test_surface_compiler.py` (create)

**Interfaces:** as frozen above.

- [ ] **Step 1: Failing tests.** Fixture: tmp dept dir with a minimal `subgraphs.json` (two subgraphs, `SG-ALPHA` with 2 nodes, `SG-BETA` with 1 node, one `untraced_allowed` entry) and an empty dir otherwise. Tests:

```python
def test_generate_creates_full_surface(fake_dept): ...
    # AGENTS.md, CLAUDE.md, ROUTER.md, 01_alpha/CONTEXT.md,
    # 01_alpha/references/README.md, 02_beta/... exist; ROUTER.md lists both
    # workspaces and contains the unknown-route STOP sentence; CLAUDE.md is
    # exactly 3 lines; every generated file contains a BEGIN and END marker

def test_generate_is_idempotent(fake_dept): ...
    # generate twice -> second pass writes byte-identical files

def test_human_region_survives_regeneration(fake_dept): ...
    # append owner prose after the END marker in 01_alpha/CONTEXT.md,
    # touch subgraphs.json (add a node to SG-ALPHA), regenerate ->
    # prose still present verbatim AND the new node appears in the chain

def test_check_surface_skips_unadopted(fake_dept): ...
    # before any generate: check_surface == []

def test_check_surface_fails_on_drift(fake_dept): ...
    # generate, then add a node to subgraphs.json without regenerating ->
    # check_surface returns a failure naming the CONTEXT.md file

def test_check_surface_fails_on_mangled_markers(fake_dept): ...
    # delete an END marker line -> failure names the file and says markers

def test_qa_includes_surface_key(fake_dept): ...
    # graphs.qa(dept) dict has "surface"; ok False when drift present

def test_generated_regions_never_contain_todo(fake_dept): ...
    # no 'TBD'/'TODO' in any generated output; empty human region text is
    # the explicit '_No owner notes yet._'
```

- [ ] **Step 2: RED** — `python3 -m pytest tests/test_surface_compiler.py -v` all fail (module absent).
- [ ] **Step 3: Implement.** Generation is pure string-building from the parsed subgraphs: deterministic ordering, no timestamps inside generated regions (idempotency), sha over the generated body embedded in the BEGIN marker is NOT required — the checker recomputes expected content and diffs, which is simpler and prints real differences. `check_surface` regenerates in memory and compares only the marker-bounded regions of each file (missing file = fail; extra NN_ folder not in topology = fail; human regions ignored). Wire `qa()`: `result["surface"] = surface_compiler.check_surface(dept_dir)` and include in `ok`.
- [ ] **Step 4: GREEN** — full `tests/` suite green (existing graphs tests must not regress).
- [ ] **Step 5: Commit** — `git commit -m "feat(factory): compiled clief-notes surface — generator + drift check wired into qa"`

### Task 2 (coordinator only): generate the podcast surface + land

- [ ] Run `python3 -m factory.surface_compiler generate --dept-dir departments/podcast`; review every generated file by eye.
- [ ] `python3 loopfactory.py validate --name podcast` → ok true including surface.
- [ ] `python3 loopfactory.py check` full green.
- [ ] Commit surface files. No re-pin (no runtime changes) — confirm `qa --name podcast` still zero mismatches.
- [ ] Work Ledger checkpoint.

## Self-Review Notes

- Spec C1 coverage: compiled ROUTER/folders/CONTEXT skeletons YES; humans-write-prose-never-edges YES (marker regions); pairwise drift CI YES (subgraphs → surface; procedural-graph.md prose stays human turf — the machine side of the pairwise check is subgraphs.json, which validate already lints against the graph doc's node table via traceability); references half-life enforcement is the README rule this phase — the expiry FAIL belongs to the reference-file check when references exist (next surface iteration, noted honestly).
- AGENTS.md stays a routing file (<50 lines generated); department knowledge keeps living in knowledge/ and CONTEXT.md human regions.
