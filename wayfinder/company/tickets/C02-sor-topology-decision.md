# C02 — Decide system-of-record topology per entity domain

Status: open · Type: grilling · Claimed: — · Blocked by: [C01]

## Question

Given C01's inventory: which system is the declared SoR for each entity
domain (sales contacts, podcast guests, referrals, content assets, goals
themselves)? Is the department event log one local store, one shared store,
or per-department files with an estate merge? Do we need a real database
(SQLite/AWS) or do append-only JSONL + rebuildable projections (the v2
pattern, ticket 07/10) stretch to company scale? HubSpot stays sales SoR for
now (owner call); this ticket decides the LOCAL truth layer and the sync
contract between local log and external SoR, including conflict rules
(who wins when HubSpot and the log disagree).
