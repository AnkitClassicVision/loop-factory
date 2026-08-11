# C09 — Prototype the blind process eval: synthetic gap fixture

Status: open · Type: prototype · Claimed: — · Blocked by: —

## Question

Build the first synthetic gap fixture cheaply and see what it teaches:
inject a fake inbound guest referral (fixture thread, fixture ledger entry)
into a shadow copy of the podcast funnel and verify the loop detects,
harvests, and acts within SLA, with zero external effects. This is proof
condition 2 in miniature and the eval class that WOULD have caught the
57→16→0 failure (output evals could not: there was no output). Deliverable:
the fixture design + a runnable harness sketch + what a "process golden set"
looks like as a factory template, for C03's eval-criteria field to reference.
