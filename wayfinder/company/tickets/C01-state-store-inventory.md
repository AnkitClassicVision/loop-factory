# C01 — Inventory every state store and entity-state record

Status: open · Type: research · Claimed: — · Blocked by: —

## Question

Before deciding SoR topology (C02): what state stores actually exist today
across loop-factory, /mnt/d_drive/repos/podcast, and hubspot-daily-2? For
each: what entity domain it holds (contacts, guests, referrals, episodes,
deals), who reads it, who writes it, whether transitions are evented or
overwritten, and where two stores hold the same entity with no sync (the
Gina case: Gmail thread state vs REFERRALS.json vs HubSpot). Output: a table
of stores × domains × readers/writers × collision points, written to
`wayfinder/company/research/c01-state-inventory.md`.
