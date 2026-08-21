# FOOUND — Product Laws

*Frozen August 21, 2026 (Plan v3.1). These are acceptance rules, not aspirations.
Any change that violates one of these is rejected in review, whoever wrote it.*

**LAW 1** — Carlos may improve FOOUND. Carlos must not need to operate a
client's FOOUND. Backend/product work (adapters, prompts, parsers, ontologies,
scoring, monitoring) is always allowed. Client-specific work (authoring a
client's vocabulary, writing their Memory, curating their watchlist, deciding
their readiness, pressing their Activate) is a product defect, even if unseen.

**LAW 2** — FOOUND never implies the system did what a human did, and never
needs a human to do what it promised the system does.

**LAW 3** — The active Working Brief changes only through client-confirmed
intent, or through system capability changes that do not alter client intent.
Nothing becomes a new Working Brief version without the client's explicit
confirmation of the affected change.

**LAW 4** — Behavior may personalize judgment inside the Brief (ranking,
emphasis, tie-breaking, exploration); it may never silently rewrite the Brief
(geography, hard exclusions, role direction, stage policy, compensation floor).
Behavior surfaces tension and proposes; only the client freezes.

## The operator boundary

**System repair allowed:** retry a failed job or email · fix corrupted state ·
repair adapters/parsers · improve generic prompts and ontologies · regenerate
after a generic fix · restore a client to last-known-valid config.

**Client substitution prohibited:** choose a client's geography · edit their
Mirror beliefs · author their vocabulary or watchlist · approve their Brief ·
decide their readiness · commission, pause, resume, or publish on their behalf.

Both kinds are logged, separately: the System Intervention Log (may stay
nonzero) and the Client Substitution Log (№002: discover every entry;
№003: zero — every entry is a product defect with a ticket).

## Standing invariants (carried from №001, still binding)

Green must mean delivered · reading is shared, judging is personal · private
state is never rendered on a public path · BEHAVIOR never silently overwrites
SELF · MODEL never weakens a HARD CONSTRAINT · hard constraints are
exclusionary gates applied before ranking, never weights · unknowns are named,
never filled · confidence mechanics hidden, human-legible provenance always
shown · Candidate never delays activation · the edition is an authored
artifact with a date, never a dashboard.
