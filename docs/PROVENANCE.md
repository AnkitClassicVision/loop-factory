# Provenance

This repo is an extraction + generalization of the department-factory work in
the private `open-engine` repo (branch `feat/open-loop-driver`, 2026-07-21).
Design authority: `open-engine/docs/superpowers/specs/2026-07-21-department-factory-design.md`
(v2.0, red-teamed: 6 lanes, 3 model families, 54 findings folded) and the
loop-factory completion plan of the same date.

| loop-factory file | Source | Change |
|---|---|---|
| factory/heal_ladder.py | open-engine src/factory/heal_ladder.py | verbatim |
| factory/release.py | open-engine src/factory/release.py | verbatim |
| factory/human_in_the_loop.py | open-engine src/factory/human_in_the_loop.py | verbatim |
| factory/estate_manager.py | open-engine src/local-scripts/open_engine_estate_manager.py | import paths only |
| factory/estate_registry.py | open-engine src/local-scripts/open_engine_estate_registry.py | verbatim |
| factory/scaffold.py | open-engine src/factory/scaffold.py | generalized: owner param, valid-YAML charter template, README points at factory/ paths (was departments/sales/) |
| factory/manager.py | open-engine departments/sales/runtime/manager.py | GENERALIZED — this was the factory/instance mixing: sales thresholds removed from code (charter is source of truth via charter_loader), department required, brief title parameterized |
| factory/graphs.py | open-engine departments/sales/tools/validate_subgraph.py | generalized + added traceability check + release-drift gate |
| factory/charter_loader.py | new | validated charter loading (fail-closed) |
| factory/memory.py | new | local-first records + pluggable backend seams |
| kernel/* | open-engine src/kernel/* | verbatim (incl. GLM-adversarial-review hardenings) |
| templates/department_daily.sh.template | open-engine src/factory/ | verbatim |
| templates/systemd/* | modeled on open-engine kernel confinement task | new |
| interview/* | modeled on the sales F1 interview artifact + §6.1 contract + the grill-me skill | new |
| runbooks/* | condensed from design spec §6, §8, §11, §13 | new |
| tests/test_heal_ladder, test_release, test_factory_human_loop, test_kernel_receipts, test_kernel_capabilities, test_kernel_hardening | open-engine tests/ | path fixes only |
| tests/test_scaffold.py | open-engine tests/ | points at factory/manager.py + factory/estate_manager.py |
| tests/test_manager.py | open-engine tests/test_sales_manager.py | de-sales'd |
| tests/test_graphs.py, test_charter_loader.py, test_memory.py | new | cover the new modules |

Deliberately NOT copied: the sales department itself (it stays in open-engine
as `departments/sales/` — an instance, not factory machinery), Linear/Hermes/
Telegram integrations, S3/terraform, and any owner-specific identity (emails,
BCC addresses, internal domains, paths).
