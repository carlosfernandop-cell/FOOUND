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
approved gated functions (`invite_agent`, `provision_agent`,
`commission_agent`, `pause_agent`, `resume_agent`, `archive_agent`) ·
Working Brief versions and the one-active invariant ·
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
| `agents` (own) | read | born only via `provision_agent()`; later state transitions only via functions; no client INSERT |
| `candidates` (own) | read | publication flow later, gated |
| `invite_agent(email)` | execute (owner / agent_no=1) | records `invitations` (`sent`, reserved serial); does not INSERT `agents`; does not copy №001 |
| `provision_agent()` | execute (authenticated) | first session for an invited email INSERTs `agents` (`user_id`, reserved `agent_no`, `invited`); existing auth identity is reused; uninvited → `blocked:not_invited` |
| `agent_config`, `sources`, `agent_sources`, `market_seen`, `invitations` | none | engine/service territory; invitations are written only through `invite_agent` |

## Invite provision (V1 — migration 012)

*Door 1 companion. App expresses invite/session intent. Supabase
enforces who may become an agent. Engine does not copy №001.*

- No public signup. Uninvited emails cannot receive an `agents` row.
- `invite_agent(email)` is owner / `agent_no=1` only. It records a
  `sent` invitation and reserves the next serial (`max(agents.agent_no,
  invitations.agent_no)+1`). Re-invite of the same pending email is
  idempotent (`sent`). An email that already has an agent returns
  `blocked:already_agent`.
- `provision_agent()` is the first-session gate. It matches
  `auth.users.email` (existing identity — including the spike user)
  to a `sent` invitation, INSERTs `agents (user_id, reserved agent_no,
  state='invited')`, and marks the invitation `accepted`. It does not
  mint a second auth user. It does not copy memory, briefs, or editions
  from №001. Already provisioned returns the current state.
- `invitations` stays service-territory (no client policies). The app
  calls the two functions; it never INSERTs `agents`.
- Prove 012 on disposable Postgres only — never the live FOOUND
  project — via `sql/dev/run_012_harness.sh` or the `migration-012`
  CI job (`postgres:16` service, database `foound_012`, no production
  credentials). The harness applies `test_harness.sql` +
  `000_harness_base.sql` + `005` + a local-only `agents_owner_read`
  policy + `012` + `test_migration_012.sql`. It does not apply 006–011.

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

## Working Brief authority

*Locked August 26, 2026. Engine law. There is no Brief-generation prompt
yet; when one exists, the model must see these subjects and this
authority law. Until then this section is the engine source of truth.
`briefs.content` remains unstructured jsonb — this is contract text, not
a schema migration. The app renders Brief; it does not invent subjects
or grant authority.*

### Subjects (few, grouped, this exact order — do not invent more)

1. **THE MOVE** = Ambition (top priority, one or several; stated on
   intake then hunted; not yet a confirmed Memory handle)
2. **ROLE SPACE** = Craft (Seat and Scope nest here, not separate Brief
   lines)
3. **WHERE** = Geography

MARKET / STILL LEARNING / READINESS have no subject yet. Do not add
them. AVOID is off until a confirmed hard no.

Titles only if the subject exists. No empty titles, ghost rows, empty
handles.

### Authority

- Confirmation is understanding, not authorization. `confirm_memory`
  means this is true of me. It does not authorize hunt, edition, or
  wake.
- The Working Brief is the first place confirmed Memory gains
  behavioral authority. Until a Brief is active, confirmed Memory is a
  record only.
- 27 confirms are the record, not the contract surface. Do not flatten
  confirmed rows into hunt terms. Do not use №001's path as a template
  for other clients.
- LOOK FOR finds (the hunt eight in `SYSTEM_PROMPT`). Brief authorizes.
  Editions and hunt compile from the active Brief only.
- `commission_agent()` remains the put-to-work gate. No edition from
  Memory alone. No wake from a Brief merely existing.

## First-real-hunt vertical slice (v1)

*Locked with the first-real-hunt slice. App expresses intent. Supabase
enforces state. Engine interprets. Path: active Brief → compile →
readiness → commission recovery → one manual hunt → one private
editions row.*

- Compile only what the active Brief authorizes (`briefs.content`).
  Confirmed Memory never becomes search authority. Do not flatten
  confirms into hunt terms. Do not write `agent_config`.
- v1 may see subjects titled THE MOVE / ROLE SPACE / WHERE on №001's
  Brief. Those three labels are **not** permanent engine architecture.
  The compiler walks titled units generically.
- `compiled_config` (engine-written) carries: `subjects_used`,
  `include[]`, `exclude_type[]`, `accepted_locations[]`,
  `search_queries[]`, `seat_cap` (few, default 5), `compiled_at`,
  `engine_sha`, `readiness_reasons[]`, and a temporary-architecture
  note field.
- Hunt jobs `compile_brief`, `refresh_readiness`, and `first_edition`
  are claimed by `hunt_runner.py`, not `synthesis_runner.py`. Manual
  fire only (`hunt.yml` workflow_dispatch). No overnight schedule.
- `first_edition` writes one private `editions` row. `publish_public`
  is false. Do not call or merge the public Shortlist publisher
  (`job_alerts.yml` / `docs/` GitHub Pages / `publish_public`).
- Zero seats is a successful empty edition. `jobs.error` is technical
  failure only (`no_active_brief` / `no_compiled_config` /
  `readiness_blocked` / named adapter errors).
- v1 market memory is personal: this agent's prior private
  `editions.payload` only. Do not use `public.market_seen` as personal
  history.
- Edition `html` is a machine artifact for the locked At Work picture:
  seats as `{id, handle, line}`. No dummy seats.
- `commission_agent()` at_work recovery (migration 011): if already
  `at_work` AND readiness is `ready` AND editions count is 0 AND there
  is no queued/running/done `first_edition` job → INSERT `first_edition`
  (payload `brief_version`) and return `at_work`. No state reset. No
  readiness bypass. Existing non-at_work gates unchanged.
  Prove 011 on disposable Postgres only — never the live FOOUND
  project — via `sql/dev/run_011_harness.sh` or the `migration-011`
  CI job (`postgres:16` service, database `foound_011`, no production
  credentials). The harness applies `test_harness.sql` +
  `000_harness_base.sql` + `005` + `006` + a local-only
  `agents_owner_read` policy + `011` + `test_migration_011.sql`.
  It does not apply 007–010.

### Judgment (this slice)

Collected candidates become seats by a **ranking/judgment** step, not
filter-then-cap. `job_alerts.rank_with_fit` is intentionally **not**
reused: it scores against Candidate `profile.md` via Anthropic + JD
fetches, requires `AgentConfig`, and logs titles — Shortlist machinery
this slice must not call. v1 judgment in `hunt_runner.judge_seats` is
deterministic and Brief-only: eligibility gates (include / exclude /
location), then a numeric score for title fit and location fit against
`compiled_config`, then rank, then `seat_cap`. `survived_because`
names those judgment reasons (`title_fit`, `location_fit`,
`exclude_cleared`, `ranked_above_peers`, …). This is sufficient to
prove the first real edition exercises judgment. It is **not**
Shortlist `rank_with_fit`, **not** overnight market reading, and **not**
editorial prose.

### role_key precedence (NEW / RESURFACED)

Personal history is keyed by `role_key`. Precedence, first match wins:

1. stable provider posting ID on the adapter row (`posting_id` /
   `provider_id` / `external_id` / `job_id` / …) → `id:<normalized>`
2. else canonical job/apply URL (lowercase host, strip `www.`, trailing
   slash, fragment, and utm/gclid/fbclid/ref/query junk) → `url:<canonical>`
3. else explicit normalized fallback `tcl:<title>|<company>|<location>`

Two distinct openings must not collapse. A minor title tweak on the
same posting ID or URL must not look new. `title|company` alone is
not durable enough and is no longer the identity.

## Readiness (v1 temporary architecture)

*This is temporary architecture, not the permanent readiness
representation. We are avoiding new schema.*

- Deterministic READY or BLOCKED with explicit reasons.
- Persist as `briefs.readiness = 'ready' | 'not_ready'`.
- BLOCKED reasons live inside `briefs.compiled_config`
  (e.g. `readiness_reasons[]`), plus a `readiness_architecture` note
  that this representation is temporary.
- Never write `'limited'` from this slice. Never infer READY from
  `agents.state = at_work`.
- BLOCKED if there is no active Brief or the Brief does not authorize
  enough to hunt (v1: no usable hunt authority in content — need
  include terms and accepted locations).
- READY if compile produced an executable hunt config from authorized
  Brief subjects.
- Schema still *allows* `'limited'` (005) and `commission_agent()`
  still honors the existing limited-ack gate on non-at_work paths.
  This slice does not invent limited-ack behavior and does not write
  the value.

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
