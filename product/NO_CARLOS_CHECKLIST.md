# FOOUND — The No-Carlos Checklist

*The acceptance suite. Frozen August 21, 2026. Run in full at Phase 3.5
(internal rehearsal) and again as №003's acceptance. The question for every
line: "If Carlos disappeared for 30 days, could the client still do this?"
A No is a product gap, never an ops issue.*

## Act without Carlos

- [ ] Accept invitation; authenticate; recover access after a failed email
- [ ] Upload evidence; see a parse failure handled (retry / paste / continue without)
- [ ] Delete an uploaded source; see the reduced-evidence-support behavior
- [ ] Add links; add "companies that matter"; say "more like this" / "stop prioritizing"
- [ ] Correct a Mirror row (edit / reject / explain) and see the ledger receipt
- [ ] Correct the Brief conceptually and see the causal receipt with effective time
- [ ] See a compile failure keep last-known-valid and allow retry
- [ ] See readiness state; on LIMITED, explicitly acknowledge the named gap (stored)
- [ ] On NOT READY, use a recovery path (broaden concept / add companies / wait honestly)
- [ ] Commission ("Put me to work") — double-press mints exactly one first edition
- [ ] Read the first edition signed-in; a second account provably cannot
- [ ] Cast PASS with reason, APPLIED, LOOK AGAIN; UNDO each
- [ ] Make one meaningful post-activation reconfiguration; see next-edition effect
- [ ] Pause; resume; archive — each idempotent, no state corruption
- [ ] Publish the Candidate page by explicit act; unpublish it

## Understand state without Carlos

- [ ] Tell whether the agent is active, paused, or archived
- [ ] Tell which Brief version is active and whether a change has taken effect
- [ ] Tell whether today's edition succeeded ("edition failed to finish" is honest, agent stays at work)
- [ ] Tell whether market coverage is limited, and what the named gap is
- [ ] Tell whether a source failed
- [ ] Tell whether FOOUND is waiting on them

## Persistence and isolation (Phase 3.5 additions)

- [ ] Edit Mirror → refresh → persists; edit Brief → logout/login → approved persists, proposed where left
- [ ] Commission → return later → edition still references the right brief_version
- [ ] Users A and B concurrent: no edit crossover, no route leaks, no cross-user
      file access, no cross-RLS Brief reads, Candidate publication isolated

## Structural rule

No client-owned lifecycle transition requires an operator-only database
mutation. commission / pause / resume / archive are client-invoked,
database-gated, and idempotent.
