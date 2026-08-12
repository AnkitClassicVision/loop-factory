# C08 — Untrusted-input boundary: data-not-instruction, autonomy demotion

Status: open · Type: grilling · Claimed: — · Blocked by: —

## Question

The concrete rule set for external content (inbound email, web pages, CRM
notes, calendar bodies) entering LLM contexts: how is it marked as data,
what sanitization/quoting applies, and what does "any unit it influences
runs one autonomy level lower" mean operationally (auto-send → draft-only?
draft → propose?). First real surface: inbox_review.py reads attacker-
controlled email and drafts replies; its REFERRAL draft-only rule is the
existing instance of the pattern. Decide the general mechanism so raising
autonomy on ingestion paths (the whole point of this map) does not open a
prompt-injection lane into the Goal Engine.
