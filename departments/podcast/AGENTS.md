<!-- GENERATED:BEGIN section=agents source=subgraphs.json -->
# Department Agent Surface

## Routing summary

- `01_watchdog/` routes to `SG-WATCHDOG`.
- `02_dag_supervision/` routes to `SG-DAG-SUPERVISION`.
- `03_pipeline/` routes to `SG-PIPELINE`.
- `04_publishday/` routes to `SG-PUBLISHDAY`.
- `05_manifest/` routes to `SG-MANIFEST`.
- `06_heal/` routes to `SG-HEAL`.

## Invariants

- Route only through the workspace table in `ROUTER.md`.
- Treat `subgraphs.json` as the machine topology source.
- Stop when no workspace matches; never guess a route.
- Keep owner prose outside generated marker pairs.
<!-- GENERATED:END section=agents -->

_No owner notes yet._
