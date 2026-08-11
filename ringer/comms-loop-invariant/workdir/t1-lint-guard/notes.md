# Comms-loop-invariant lint guard

## Read for conventions

- `factory/graphs.py`: `validate_subgraph`, `validate_subgraphs`, `check_traceability`, and the existing failure-string style.
- `loopfactory.py`: `cmd_validate` and its map-QA call.
- `departments/podcast/subgraphs.json`: existing read-only node and subgraph shapes.
- `departments/social/subgraphs.json`: existing dispatch, model-capable, and guard node shapes.
- `tests/test_kernel_receipts.py`: import-by-path and pytest assertion style.

## Validate-flow hook

`loopfactory.py cmd_validate` calls `graphs.qa(dept_dir)`. `qa` loads the department's `subgraphs.json`, calls `validate_subgraphs(data)`, and that function calls `validate_subgraph(sg)` for each subgraph. The new rule is inside `validate_subgraph`'s existing per-node loop, so violations flow into the same `fails` list and then `result["lint"]` used by `cmd_validate`.

## Commands and results

- `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_comms_invariant_lint.py -q`: PASS, 5 passed in 0.02s.
- `python3 loopfactory.py validate --name podcast`: PASS, overall `ok: true`, empty lint and traceability lists.
- `python3 loopfactory.py validate --name social`: PASS, overall `ok: true`, empty lint and traceability lists.
- `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -q`: PASS, 600 passed and 1 skipped in 8.67s.
- Ringer ownership check: FAIL because it detected pre-existing changes outside T1 ownership at `departments/podcast/releases/50b79dc454f5082d/manifest.json`, `departments/podcast/releases/current`, and `departments/social/runtime/social_daily.sh`. These paths were not modified or removed because the task explicitly forbids edits outside the two owned repo paths. Read-only inspection showed that the tracked diffs change the podcast release pointer and social engine defaults, which are unrelated to this lint guard; their filesystem modification times also predate the owned implementation edits.

## Assumptions

- `emits_ask` activates the rule only when its value is the JSON boolean `true`; absent, false, and other values do not activate it.
- A valid `return_path` is a string containing at least one non-whitespace character.
- A valid `return_sla_hours` is an integer or float greater than zero; booleans are rejected even though Python treats them as integers.
- Unknown node fields are ignored. The green test includes one to prove they do not crash or affect the guard.
