<!-- GENERATED:BEGIN section=agents source=subgraphs.json -->
# Department Agent Surface

## Routing summary

- `01_received/` routes to `SG-RECEIVED`.
- `02_qualified/` routes to `SG-QUALIFIED`.
- `03_conversation_live/` routes to `SG-CONVERSATION-LIVE`.
- `04_nervous_parked/` routes to `SG-NERVOUS-PARKED`.
- `05_booked/` routes to `SG-BOOKED`.
- `06_held/` routes to `SG-HELD`.
- `07_sense/` routes to `SG-SENSE`.

## Invariants

- Route only through the workspace table in `ROUTER.md`.
- Treat `subgraphs.json` as the machine topology source.
- Stop when no workspace matches; never guess a route.
- Keep owner prose outside generated marker pairs.
<!-- GENERATED:END section=agents -->

_No owner notes yet._
