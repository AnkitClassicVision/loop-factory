<!-- GENERATED:BEGIN section=router source=subgraphs.json -->
# Workspace Router

| Workspace folder | Subgraph id | Node count | Concept refs | DONE means |
|---|---|---:|---|---|
| `01_watchdog/` | `SG-WATCHDOG` | 11 | C1, C3, C4, C12, C13, C16 | every estate unit in the charter inventory was sensed this run (a missing unit is a silent-gap FAIL) (+2 more) |
| `02_dag_supervision/` | `SG-DAG-SUPERVISION` | 1 | C1, C2, C11, C16, C19 | the pipeline's hashed DAG projection validated fresh (stale or missing projection is an alarm, never a skip) (+1 more) |
| `03_pipeline/` | `SG-PIPELINE` | 2 | C1, C3, C4, C15 | guest pipeline counts measured from FUNNEL-LEDGER evidence, never from stage labels (+1 more) |
| `04_publishday/` | `SG-PUBLISHDAY` | 1 | C1, C3, C10, C16 | publish-day artifacts verified by 10:30 ET or an exact missing-proof block recorded |
| `05_manifest/` | `SG-MANIFEST` | 2 | C6, C7, C10, C15 | every guest/episode manifest checked for completeness against the declared expectation manifests (+1 more) |
| `06_heal/` | `SG-HEAL` | 4 | C5, C11, C12, C14 | every open incident offered exactly one allowlisted playbook or a recorded refusal (unknown classes refuse, never improvise) (+1 more) |

a task matching no workspace STOPS rather than guessing.
<!-- GENERATED:END section=router -->
