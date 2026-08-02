---
ticket: 06-agentspace-compat-check
type: research findings
scope: ingestion-path constraints for the v2 estate reporting contract (ticket 10)
NOT: a platform recommendation — platform choice stays out of scope
---

## Part (a) — local `/mnt/d_drive/repos/AgentSpace` repo

**This is not Google Agentspace.** It is an unrelated open-source project (same
name, different product): "AgentSpace: Human + Agents. One Team. One
Workspace" — an agent-native collaborative workspace (HKUDS-style OSS,
hosted at `hire-an-agent.online`) for running Claude Code / Codex / OpenClaw
/ Hermes agents as "digital employees" with scheduling (AgentRouter),
permissions/approvals, Feishu/Slack integrations, and a Postgres-backed
daemon (`agent-space-daemon-0.1.3.tgz`). Files present: `README.md`,
`README_ZH.md`, `Target.md`, `CONTRIBUTING.md`, `TODO/`, `packages/daemon/`,
`deploy/` (`FOUNDER_EXECUTION_SHOWCASE.md`, `REMOTE_DAEMON_TEST.md`),
`scripts/feishu/`. No mention of Google, Gemini, or Discovery Engine
anywhere in the repo. `Target.md` confirms scope: "AgentSpace is a
multi-user, multi-agent workspace where human teams and digital employees
share context, permissions, runtimes, approvals, and delivery outputs."

**No prior evaluation notes exist here relevant to Google's product.** There
is nothing in this repo to carry forward for the ingestion question — it
answers a different question (agent orchestration/governance, not a data
board/search surface). `loop-factory/wayfinder/MAP.md` and `map.md` already
have the correct pointer: Ankit is evaluating **"Gemini Enterprise, ex-Agentspace"**
(Google Cloud's product, renamed from Agentspace to Gemini Enterprise in
2026) as the eventual visual-board consumer. `resume.md` at the loop-factory
root has no mention of Agentspace either. Flagging this name collision so it
doesn't get conflated in later tickets — if anyone opens
`/mnt/d_drive/repos/AgentSpace` expecting Google product notes, they'll find
none.

## Part (b) — Google Gemini Enterprise (formerly Agentspace) ingestion paths

Source: official Google Cloud docs at `docs.cloud.google.com/gemini/enterprise/docs/...`
(pages carried "Last updated 2026-07-17" and "2026-07-28" footers — current
as of this check, 2026-08-02). Not paywalled; all verifiable from public docs.

**Ingestion is connector/data-store based, not a generic webhook.** Two
delivery models exist:

1. **Push (incremental upsert)** — direct calls to the **Discovery Engine
   API** to upsert documents in near-real-time. Suited to streaming/frequent
   small updates. This is the closest fit to "emit an event and it lands."
2. **Pull (comprehensive sync)** — Google's documented "recommended"
   approach: stage data in **Google Cloud Storage** (or BigQuery for
   structured data) and let a scheduled connector sync pull it. Supports
   full syncs (adds/updates/deletes) and incremental syncs (adds/updates
   only). Entity-data sync frequency is selectable: every 3, 6, 12 hours, or
   1/3/5/7 days (identity/ACL sync can go as fine as every 30 minutes).

**Data shape, concretely:**
- **Structured data** (the shape a reporting/rollup feed would use): **JSON
  Lines / NDJSON**, one record per line, each record needs an `id` field
  (required string) plus either a `jsonData` string field or a `structData`
  JSON object field carrying the payload. Schema can be auto-detected on
  import or declared via API (JSON Schema style, with `keyPropertyMapping`).
  Importable from GCS or BigQuery.
- **Unstructured data** (documents): PDF, HTML, DOCX, PPTX, XLSX, XLSM — not
  relevant to a status/rollup feed, but relevant if the department ever wants
  runbooks/reports indexed for search.
- Every ingested item carries **ACL/identity metadata** as a first-class
  concept (identity sync is separate from entity sync) — access control
  isn't bolted on after the fact, it's part of the ingestion contract.

**Custom connector fallback:** if no prebuilt connector fits, Google's
"custom connector" path is exactly "call the Discovery Engine API yourself
with documents in their standard JSON format" — i.e. the push model above,
self-built. No prebuilt "generic status board" connector exists; you either
use a named connector or speak the Discovery Engine API/NDJSON directly.

**Named connectors relevant to this map's other surfaces:** the prebuilt
connector library (80+, split GA/Preview) includes **Linear** (public
preview tier) alongside GitHub, GitLab, Jira, Confluence, ServiceNow,
Slack, Notion, Monday, Asana, and others. So Linear-as-a-surface and
Gemini-Enterprise-as-a-surface are not mutually exclusive paths — Gemini
Enterprise could ingest Linear's data itself as a downstream connector,
which is a fact worth knowing for ticket 10 but is a platform-selection
question, out of scope here.

**Unknowns (not verified, do not guess):**
- Exact custom-connector auth model beyond OAuth for the named first/third-
  party sources — the third-party-connector page did not give a universal
  credential spec.
- Any hard payload-size or field-count ceiling per NDJSON record.
- Cost/quota implications of frequent push-model upserts at high frequency.
- Whether a fully generic "webhook receiver" mode exists outside the
  GCS/BigQuery-staged pull model — nothing in the fetched docs describes one.

## Constraints list for the v2 estate reporting contract (ticket 10)

Framed as "emit X in shape Y so any of Agentspace(Gemini Enterprise) / Linear
/ a static HTML board can consume it later" — not a recommendation to build
toward one of these specifically.

1. **Emit one record per line, NDJSON, not a single JSON blob.** Gemini
   Enterprise's structured-data import wants JSON Lines; NDJSON is also
   trivially diffable, appendable, and greppable for a static-HTML board or
   a Linear-side sync script. A single nested JSON document would need
   reshaping for Gemini Enterprise; NDJSON needs none.
2. **Give every record a stable, unique `id` field, string-typed.** This is
   a hard requirement on the Gemini Enterprise side (required for
   structured-data import) and is good practice regardless (idempotent
   upserts for any consumer, dedup for a static board).
3. **Keep the payload as a flat-ish JSON object per record** (department
   name, run id, step, status, timestamp, blockers, cost/token rollup,
   drift flags, autonomy state) rather than deeply nested structures —
   this maps directly onto `structData`/`jsonData` without a translation
   layer, and is equally easy for Linear-issue-body templating or an HTML
   table renderer.
4. **Treat each record as append-only / event-shaped, with a last-write-wins
   status snapshot derivable from it.** This satisfies both ingestion
   models on the Gemini Enterprise side (push-as-you-go upsert, or batch
   pull on a schedule) without redesigning the emitter for whichever model
   gets picked later.
5. **Do not bake in a push (webhook) assumption as the only path.** Google's
   own "recommended" path is scheduled pull from staged storage (GCS/
   BigQuery), not push. The contract should equally support "a cron job
   reads the latest NDJSON file" and "an event fires a direct upsert call" —
   i.e. the estate should write the NDJSON somewhere stat-able/pollable
   (a file, a small HTTP-servable path, or object storage), not assume a
   live socket on the other end.
6. **Carry identity/ownership metadata as its own field(s) separate from the
   status payload** (e.g. `owner`, `tenant`, `visibility`), because Gemini
   Enterprise treats ACL/identity sync as a distinct concern from entity
   data sync. Even if unused by an interim static board, keeping it as a
   separate field now avoids a schema break later.
7. **Assume no generic "just POST me JSON" receiver exists on the Agentspace
   side.** Whatever ships first (interim board, later a real connector) has
   to either match a named connector's expected shape or speak the Discovery
   Engine API/NDJSON-to-GCS convention directly — there's no lower-common-
   denominator webhook fallback documented. Plan for "we write files a
   sync step can pick up," not "we push to an endpoint Google gave us."

## Sources

- [What Is Gemini Enterprise (Formerly Agentspace)? 2026 Guide](https://atlan.com/know/ai-agent/ai-agent-applications/google-agentspace/)
- [Gemini Enterprise release notes](https://docs.cloud.google.com/gemini/enterprise/docs/release-notes)
- [Connectors for Gemini Enterprise app](https://cloud.google.com/gemini-enterprise/connectors)
- [Introduction to connectors and data stores](https://docs.cloud.google.com/gemini/enterprise/docs/connectors/introduction-to-connectors-and-data-stores) (last updated 2026-07-17)
- [Connect a third-party data source](https://docs.cloud.google.com/gemini/enterprise/docs/connectors/connect-third-party-data-source) (last updated 2026-07-28)
- [Custom connector overview](https://docs.cloud.google.com/gemini/enterprise/docs/connectors/custom-connector) (last updated 2026-07-17)
- [Provide or auto-detect a schema](https://docs.cloud.google.com/gemini/enterprise/docs/provide-schema)
- [Prepare data for custom data sources](https://docs.cloud.google.com/gemini/enterprise/docs/connectors/prepare-data)
- [Import from Cloud Storage](https://docs.cloud.google.com/gemini/enterprise/docs/connectors/connect-cloud-storage)
