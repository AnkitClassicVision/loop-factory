# D1 — digest cadence + trigger class (resolved)

type: grilling · status: resolved · decided: 2026-08-03 (owner destination sign
covers the throwaway's operating envelope; no separate owner call needed — the
decision is bounded by the signed out-of-scope line "no enabled timers")

DECISION: time trigger, OnCalendar daily 07:00, Persistent=false, catch_up
skip, max_concurrent 1. Units rendered and checked (Stage 8 gate) but NEVER
enabled — the acceptance run drives cycles by hand, matching the calibration
rule (hand-executed first).

Why: mirrors the house precedent (loop-factory-triage.timer: Persistent=false,
disabled-by-default) and keeps the throwaway inert outside the test.
