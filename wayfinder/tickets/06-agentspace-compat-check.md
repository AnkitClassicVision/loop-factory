---
title: AgentSpace ingestion compatibility check
status: closed
type: research
assignee: wf-06-agentspace
blocked_by: []
---

## Question

What data-ingestion paths does Google Agentspace (the surface Ankit is
evaluating for the visual factory) accept — connectors, APIs, data formats,
push vs pull — such that the v2 estate reporting contract (ticket 10) can be
designed to feed it later without rework? Also check the local
`/mnt/d_drive/repos/AgentSpace` repo for prior evaluation notes. Output: a
short constraints list for the contract ("emit X so any of
Agentspace/Linear/static-HTML can consume it"), NOT a platform recommendation
— platform choice is out of scope for this map.

## Resolution

Local `/mnt/d_drive/repos/AgentSpace` is an unrelated OSS project (same name,
different product — an agent-orchestration workspace, no Google/Gemini
mention anywhere); it carries no usable prior notes on Google's product.
The actual target, confirmed by `MAP.md`/`map.md`, is Google Cloud's
**Gemini Enterprise (renamed from Agentspace in 2026)**. Its ingestion model:
connector/data-store based, two paths — push (Discovery Engine API
incremental upsert, near-real-time) or pull (Google's "recommended" path:
stage NDJSON/JSON-Lines in GCS or BigQuery, scheduled sync every 3-12h or
1-7 days). Structured records need a required string `id` plus a flat
`jsonData`/`structData` payload; ACL/identity metadata is a separate sync
concern from entity data. No generic webhook receiver is documented. Linear
itself is a Gemini Enterprise public-preview connector, so the two surfaces
aren't mutually exclusive later. Full findings, constraints list, and
sources: `wayfinder/research/06-agentspace-compat-check.md`.
