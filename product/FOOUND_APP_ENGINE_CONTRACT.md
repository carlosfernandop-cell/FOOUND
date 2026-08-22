# FOOUND — App ↔ Engine Contract

*Frozen August 21, 2026. One product brain (the frozen v3.1 plan), two
execution surfaces. This document is what stops them from slowly duplicating
each other's logic. Changes to this contract are reviewed changes, in the
engine repo, like any migration.*

## The operating rule

> **The app expresses intent. Supabase enforces state transitions.
> The engine interprets and produces intelligence.**

The application contract is **Supabase schema + RLS + approved database
functions** — never "the database" wholesale. Some objects are direct
RLS-scoped reads/writes; state transitions and engine work go through gates.

## Ownership

**The app owns (Lovable repo · app.foound.ai):** authentication UX · Feed
(evidence upload, links, the pair, questions) · Mirror rendering and
correction interactions · Brief rendering and concept editing · Readiness
display and recovery actions · causal-receipt presentation · the commissioning
action (calling the gate) · the owner reader (editions, archive) ·
pause/resume/archive UI · Candidate publication UI (later) · honest state
display (active? change effective? edition succeeded? waiting on me?).

**Supabase owns (schema in engine repo `sql/`):** identity · authorization
(RLS is the only isolation boundary) · client state and lifecycle ·
approved gated functions (`commission_agent`, `pause_agent`, `resume_agent`,
`archive_agent`) · Working Brief versions and the one-active invariant ·
Evidence Ledger · config persistence · edition persistence · the `jobs`
intent queue and its invariants.

**The engine owns (existing repo · Python · Actions):** evidence
interpretation and synthesis · the Role Model · Brief generation · config
compilation · market reading · readiness calculation · scoring and editorial
reasoning · edition generation · learning interpretation (verdicts → ledger)
· delivery email · system health and operator logs · schema migrations.

**The app explicitly does NOT own:** scoring · vocabulary/query generation ·
source selection · Brief compilation · readiness judgment · business-rule
enforcement (rules live in schema constraints, RLS, and gated functions) ·
client isolation (RLS's job) · any schema change.

## Access map (what the app touches, and how)

| Object | App access | Notes |
|---|---|---|
| own uploads (Storage `feeds/{uid}/{item_id}/blob`) | insert + read own prefix | object requires a matching owned `evidence_items` row; no update/delete — physical cleanup is engine work via the Storage API, never SQL |
| `evidence_items` (own) | read; insert `received` only (client-generated id, canonical path); update = request logical deletion only (`status` → `deleted`) | column-grant enforced; `reading`/`read`/`failed`, `submitted_in`, `read_at`, `deleted_at` are engine/database-owned; deletion is terminal and orphans derived memory in-database |
| `memory` (own) | read only | correction paths arrive with the Mirror contract; active memory may cite only `read`, same-agent evidence (database-enforced) |
| `briefs` (own) | read; insert PROPOSED; edit proposed only | activation never via row update |
| `editions` (own) | read | render as delivered |
| `signals` (own) | read/write | verdict loop (already live) |
| `jobs` (own) | insert intent (`synthesize`, `compile_brief`, `refresh_readiness`); read status | never run/complete; `first_edition` only via `commission_agent()` |
| `agents` (own) | read | state transitions only via functions |
| `candidates` (own) | read | publication flow later, gated |
| `agent_config`, `sources`, `agent_sources`, `market_seen`, `invitations` | none | engine/service territory |

## The job boundary (V1)

The app inserts a queued job for its own agent and watches its status —
that is the entire trigger surface. The engine claims and completes jobs:
at every scheduled pipeline run, on a lightweight jobs workflow cadence, and
on manual fire while we are piloting. `commission_agent()` enqueues
`first_edition` atomically with the state flip, so "Put me to work" is one
gate, not app logic. Poll latency is a pilot-honest tradeoff; the upgrade
path (Edge Function → `repository_dispatch` wake-up) changes speed, never
the contract. Failed jobs carry an `error` the app shows honestly, with a
client retry (a fresh intent), never an operator path. `jobs.error` is
client-legible by contract — the app renders it, never suppresses it.

## Evidence intake (V1 — migration 007)

- Feed v1 accepts **file upload (pdf/docx/txt/md, ≤20 MB) and pasted text
  only**. No links, no images — fully absent, no placeholders, no "coming
  soon." Client-supplied `mime_type`/`byte_size` are intake metadata; the
  engine validates the actual stored object and fails items honestly on
  disagreement.
- File flow is **row-first**: the app generates the item UUID, inserts the
  `received` row with canonical path `{uid}/{item_id}/blob` (original
  filename lives only in `label`), then uploads the object.
- Upload/paste triggers nothing. The handoff is one explicit client act —
  *"I'm done. Read what I gave you."* — inserting one `synthesize` intent.
  One job = one submission batch. A conflict on insert means a synthesis is
  already active: the app renders "FOOUND is already reading.", a fact, not
  an error.
- The engine runs synthesis through exactly two doors, both atomic,
  service-only: `claim_synthesis_batch(job)` (queued→running · received→
  reading · invited/commissioning→feed_submitted, non-empty claims only) and
  `finalize_synthesis(job, outcome)` (running→done/failed · feed_submitted→
  mirror_ready/commissioning). **The database refuses any other terminal
  path for a running synthesize job** (guard trigger). Job `done` never
  implies Mirror readiness — the outcome carries that judgment.
- Evidence-item states the app renders: `received`, `reading`, `read`,
  `failed` (with a client-legible `failure_reason`), `deleted` (shown as
  removed history, never vanished). The app never writes processing states.
  Swept items (still `reading` when a batch finalized) are recovery
  artifacts — the technical reason goes to engine logs, never the client
  field.
- Deletion is logical and immediate in effect (derived beliefs orphan
  in-database); physical cleanup is asynchronous engine work through the
  Storage API.

## Cross-boundary change protocol

**Cross-boundary changes require contract review — both directions.**

- The app never adds or alters schema. If Lovable needs something new
  ("Mirror needs a provenance display label"), it produces a REQUIREMENT.
  The engine side determines: existing schema already supports it · the app
  should derive it · or a reviewed migration adds it. Then this contract's
  access map is updated in the same change.
- The engine never alters an object the app consumes (columns, function
  signatures, status vocabularies, job types) without updating this contract
  in the same commit and flagging the app side before deploy.
- **If the app gets ahead of the engine, it mocks locally with fixtures.
  It never invents temporary tables, parallel schemas, or client-side
  stand-ins for engine intelligence.** (Hard rule.)

## Repos and executors

Engine repo: Claude executes; Lovable never touches. App repo: Lovable
executes; Claude reviews every diff and returns findings as correction
prompts. Nobody executes in both. Carlos is never the transport mechanism:
if a technical Git operation is needed, Claude performs or packages it.
