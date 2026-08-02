# 009 — Factory recipe versioning scheme

Status: OPEN · Type: grilling (HITL) · Claimed: — · Blocked by: — (008 resolved: incremental runner with rewrite reservation — versioning must survive a runner swap)

## Question

How is the factory recipe itself versioned (factory@version), so "Lead Follow-Up Factory v1 vs v2" comparisons are real and no live factory changes silently? Department releases are pinned today; the factory recipe is git-history only.

## Shape (to refine after 008)

- A factory version = (graph schema version, runner version, telemetry schema_version, template set hash) pinned per department release.
- Changing a live factory = new version + side-by-side comparison, never in-place mutation (per the Hermes/bee findings).
- Interacts with `factory/release.py` pinning and drift QA.

## Resolution

(pending)
