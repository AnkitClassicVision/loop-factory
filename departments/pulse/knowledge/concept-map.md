# pulse — concept map (LOCKED at F1, 2026-08-03)

Every concept traces to an interview answer in
`interview/intent-interview.md`; the procedural graph cites these ids.

- **C1 inbox notes** — plain-text fixture status notes in `inbox/`; the only
  input. No identities, no real data. (F1 Q1, Q3)
- **C2 daily digest** — one draft per cycle folding every readable note in
  exactly once; shadow artifact, never sent. (F1 Q1, Q2 digest_coverage)
- **C3 clarify ask + return loop** — unreadable note → one `clarify_note`
  ask draft in the outbox; the RETURN PATH is the digest builder reading
  `replies/` next cycle, 48h SLA; the comms-loop invariant made observable.
  (F1 Q1, Q4; ticket D2)
- **C4 objectives observation** — a deterministic sensor computes
  digest_coverage and ask_return_integrity from aggregate state only and
  writes `state/objectives_observed.json` (objectives-observed/v1). (F1 Q2)
- **C5 shadow discipline** — zero external actions at every step; timers
  rendered but never enabled; deletion at the end. (F1 Q3; ticket D1)
