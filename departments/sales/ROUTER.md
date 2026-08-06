<!-- GENERATED:BEGIN section=router source=subgraphs.json -->
# Workspace Router

| Workspace folder | Subgraph id | Node count | Concept refs | DONE means |
|---|---|---:|---|---|
| `01_received/` | `SG-RECEIVED` | 3 | C2, C10, C13 | lead exists as one row in the cohort ledger with source attribution (icaregrow, podcast_handoff, inbound, website_form, pfs_warm, pfs_cold) (+1 more) |
| `02_qualified/` | `SG-QUALIFIED` | 2 | C3, C10 | qualification bar decision recorded WITH evidence: services bar (owner/decision-maker at an ICP-fit optometry practice) OR seller bar (owner signaling exit interest within ~24 months) (+1 more) |
| `03_conversation_live/` | `SG-CONVERSATION-LIVE` | 5 | C3, C5, C6 | two-way exchange within the last 7 days: a human reply or a live conversation (opens and clicks never count) (+2 more) |
| `04_nervous_parked/` | `SG-NERVOUS-PARKED` | 1 | C3, C2 | exit happens exactly one way: a revive touch was executed (draft approved + sent via the estate gateway) OR an explicit kill reason is recorded (+1 more) |
| `05_booked/` | `SG-BOOKED` | 1 | C3 | calendar receipt exists with a confirmed time and attendee (+1 more) |
| `06_held/` | `SG-HELD` | 2 | C1, C10 | call attended, decision-maker present, >= 20 minutes (locked at readback) (+2 more) |
| `07_sense/` | `SG-SENSE` | 4 | C4, C5, C6, C8 | all four context-is-king gates ran today: cross-lane double-touch suppression, context-packet completeness, staleness truth on conversation_live, voice-check coverage (+2 more) |

a task matching no workspace STOPS rather than guessing.
<!-- GENERATED:END section=router -->
