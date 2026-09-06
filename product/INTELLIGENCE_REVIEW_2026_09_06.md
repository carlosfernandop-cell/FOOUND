# FOOUND intelligence: evidence, decisions and next gates

Prepared during 5 September Chicago evening and 6 September UTC. Local review against engine `2ceec80044f1e34f933906ec27d46c7fc8cb5fe8`, branch `intelligence-review-20260906`. This is not a production release or a change to product law.

## Founder assessment

FOOUND has a credible foundation for personal representation. It separates what the person has said, what the system understands and what the person has authorized. That distinction deserves to survive.

Its largest weaknesses are between those stages. A good role can disappear before judgment. A past judgment can survive changed evidence. Research can disagree with a recommendation without changing it. A successful scheduled job can conceal a product failure. These are not reasons to rebuild FOOUND. They are precise places where its implementation has fallen short of its own principles.

The local changes repair the stale judgment and contradictory research paths, strengthen prompt authority and remove one proven private evidence logging path. They do not certify market judgment, database concurrency or launch readiness.

## What was examined

| Part | Actual implementation inspected | Finding |
| --- | --- | --- |
| Product authority | `product/PRODUCT_LAWS.md`, contract, activation and commission SQL | Brief approval is a distinct act. Preserve it. Edition persistence does not yet enforce the same authority atomically. |
| Evidence and Memory | `synthesis_runner.py`, `candidate_context.py`, synthesis validation | Citation ownership and confirmation boundaries are valuable. Citation IDs alone do not prove that a statement follows from its evidence. |
| Brief proposal | `brief_proposal.py`, proposal generation and validation | A deterministic compiler influences how the model must phrase intent. Its vocabulary can narrow the person’s meaning. |
| Market discovery | `market_sources.py`, role expansion, title and location gates | The available market and surviving titles constrain judgment before the model reads a role. Missing location can pass the mechanical gate. |
| Ranking | `job_alerts.py`, private hunt hooks | A heuristic selects the reading budget, then a model assigns fit and reasons. Some priority and reconsider paths exceed the ordinary budget. |
| Memory of judgments | Prior edition receipts in `hunt_runner.py` | Previously insufficient evidence of compatibility. Repaired locally. |
| Deeper research | `deep_look`, private seating and rendering | Revised fit previously did not settle the actual recommendation. Repaired locally with one bounded research pass. |
| Editions and return | Persistence, daily enqueue, rendering and receipts | Pause and changed Brief races remain open. Existing editions can suppress recovery under a newer Brief. |
| Failure and recovery | Synthesis exceptions, hunt errors, queue rules | One privacy leak repaired. Unknown judgment can still look like refusal or recommendation. Database queue health was not queried. |

The private app source was not fully checked out in this review. Fable supplied product continuity and prior live observations. Engine source, local tests and those dated reports are different evidence classes.

## How FOOUND actually thinks today

Evidence enters synthesis. Synthesis proposes statements with provenance and evidence references. Confirmed active Memory supplies candidate understanding. A proposed Brief becomes authority only after the person activates it. The engine compiles that Brief into role and geography gates and selects market sources.

Those gates remove postings before the judge sees them. Personal pass and applied verdicts remove more. A heuristic allocates full reads. Model fit then drives seating, with a floor, a cap and priority rules. A leading role can receive deeper research. The edition records what was considered and becomes part of later history.

This is not one unconstrained intelligence. It is a chain of permissions, evidence, deterministic choices and model judgments. Improving the model alone cannot recover a role discarded upstream or repair an incorrect persistence boundary downstream.

## What changed locally

### A judgment now has a reason to remain valid

Reuse requires the same person, full Brief content, compiled configuration, actual candidate context, model identifier and explicit judgment protocol. The current posting must also have the same normalized evidence hash. Whitespace alone does not invalidate it. Dates and actual posting content do.

Missing posting text is not proof that the posting is unchanged. Missing or malformed receipts do not justify reuse. Confirmed changes to understanding and changes to intent reach the judge even when role and geography filters happen to stay identical. An unrelated code deployment does not automatically invalidate every judgment. Explicit reconsideration still wins. The existing age limit remains.

Receipts store hashes, not a second copy of the person’s record or posting text. They are private diagnostic data, not an authorization boundary. A model provider changing behavior behind an unchanged model name remains undetectable by this receipt alone.

### Research now changes the recommendation

A valid revised integer fit and meaningful verdict settle the actual score and reseat the edition. The first score and its reasons remain in a research receipt. Research that disqualifies the lead produces a real refusal, not a positive recommendation with contradictory text underneath.

There is still at most one deeper research pass per hunt. If another role becomes the lead, the output explicitly says it has not received that deeper pass. A considered zero can show why roles were refused. Invalid research scores are not coerced into valid ones.

This is plumbing correctness, not independent verification of every research claim. Sources and claim support still need stronger validation before treating model research as dependable evidence.

### The full active Brief stays visible

Private scoring and deeper research receive the complete active Brief separately from candidate understanding. The deeper judge no longer loses that authority when the career context exceeds its excerpt limit. The prompt identifies the excerpt and distinguishes authority, evidence, inference and unknowns. Untrusted evidence is not an instruction source.

Tests capture the actual request shape. They do not prove a real model resists every malicious posting. That requires adversarial model evaluation.

### Private evidence does not belong in public error traces

An unexpected synthesis exception could contain evidence in its message and traceback. An offline counterexample demonstrated the leak. The repaired path reports the job identifier and exception class, while preserving failure settlement. This repairs that path, not every possible logging path in the product.

## What Astra and Fable challenged

Fable identified the narrow title gate. Astra challenged the inference that recovering titles necessarily means recovering worthwhile opportunities. The resulting experiment separates broad recall from permission to recommend. Broader discovery must not silently broaden the Brief.

Fable challenged cache invalidation cost and suggested ignoring dates or accepting a failed posting fetch as unchanged. Astra rejected those shortcuts because dates can contain real requirements and missing evidence proves nothing. The chosen compromise preserves whitespace normalization, existing reading limits and stable judgments across unrelated deployments.

Fable challenged a research repair that could silently promote an unresearched lead. The implementation keeps one bounded pass and names that limitation in the result.

Astra found that early membership parsers could turn malformed output into a considered zero, lose titles beyond the batch budget or accept conflicting duplicate answers. Fable revised the prototype. Independent execution of v3 passed his 17 fixtures, then exposed four more malformed response cases. Those were returned for repair. The prototype remains outside the hunt.

On the edition authority race, we rejected a simple late Python check as insufficient. Fable developed a database locking proposal and Astra challenged lock ordering, resume recovery and the claim that first editions use the proposal stand down rule. The migration is held until an isolated concurrency harness proves both insert and replacement paths and recovery.

The actual Astra diff has been handed to Fable for reciprocal review. At this checkpoint his browser access to the local patch is waiting on Claude’s permission prompt. Architectural discussion is complete enough to guide the patch; final code signoff is not claimed.

## What deliberately remains unchanged

Brief activation, Memory confirmation, publication consent, row ownership, personal pass and applied verdicts, the ordinary reading budget and seating floor remain intact. No additional agent layer, model migration or paid call was introduced. The discovery prototype is isolated under `experiments/role_space` and is not imported into the production hunt.

The approved invitation, v9 landing, Candidate article and visual system are unchanged. No production deployment, database migration, invitation email, candidate provisioning or personal record mutation occurred.

Legacy public rendering remains structurally identical in the normalized offline render comparison against the base revision. Valid legacy public research still works; invalid `fit_after` values are now rejected by the shared parser. That is an intentional shared validation change, not an entirely private diff.

## Open gates before relying on autonomous recommendations

| Priority | Gate | Proof required | Owner |
| --- | --- | --- | --- |
| First | Edition authority at persistence | Disposable PostgreSQL sessions prove pause, activation, insert, replacement and concurrent writer order. Never publish a result under authority that was revoked before the write. Prove recovery without retry storms. | Fable implementation, Astra review |
| First | Truthful unavailable and partial judgment | A failed read is unknown, not refused. A heuristic shortlist is not a FOOUND recommendation. Engine receipts and app states must agree. Preserve a useful prior edition without pretending it was refreshed. | Joint engine and app change |
| First | Resume with an older edition | Current Brief receives work after resume even when today already has an edition under a prior version. Actual SQL queue rules and runner behavior must be tested together. | Fable implementation, Astra counterexamples |
| Next | Wider recall without wider authority | A frozen independently labeled corpus, realistic negatives, ambiguous titles, geographic restrictions and injected instructions. Measure missed valid roles, unauthorized admission, unknowns, cost and latency with the actual model. | Joint evaluation |
| Next | Evidence quality and research | Verified, inferred, conflicting and unknown requirements must remain distinct. Bind material research claims to their supporting sources. Evaluate adverse research rather than rewarding more positive matches. | Joint judgment design |
| Next | Bounded operation | Queue recovery, stuck jobs, current daily run, credentials and source health, cost cap and duplicate suppression verified against the deployed product. | Fable operational proof, Astra acceptance |

Four strict expected failures in `test_open_intelligence_gates.py` preserve the first three gaps as executable acceptance cases. They are deliberately not counted as passing safety tests. The MemoryDb authority example exposes runner behavior only. It is not a substitute for a real transactional concurrency test.

Other deeper work: optional AVOID language not becoming a hard gate, inherited contract and part time exclusions, ambiguous remote geography, finite context retention, synthesis unknowns not becoming useful questions, evidence entailment, model score calibration and per person delivery time. These need explicit semantics and tests, not a rushed accumulation of rules.

## The stronger product model

FOOUND should maintain a reasoned case for each serious opportunity, not merely a score. The case should distinguish what the person authorized, what the evidence supports, what remains unknown, why a move could matter and what would change the recommendation. This can replace ambiguity inside the current pipeline rather than add another layer of agents.

The next evaluation should ask whether FOOUND makes a better decision than its current baseline. More roles found is not sufficient. Measure valid opportunities missed, unacceptable opportunities admitted, unsupported claims, useful refusals, contradictory recommendations and the work still required from the person. Use frozen cases with reviewer labels and disagreement resolution. Reserve unseen cases for evaluation. Do not tune and grade on the same fixture.

Carlos supplies his real intentions and judges whether the reasoning represents him. He should not repair queues or translate between us. The team owns those operational burdens.

## Release and rollback discipline

The exact additive fields and app acceptance obligations are recorded in `JUDGMENT_RECEIPT_PROPOSAL.md`.

Validation at the current checkpoint: 163 tests and 15 subtests passed in the combined offline engine and isolated Fable v3 suite; four strict expected acceptance failures remain. A separate selection of synthesis unit tests passed 9 tests with 28 database dependent cases deselected. The mocked public orchestration runner passed 24 assertions. The parameterization scan passed. Normalized public rendering matched base revision bytes. Syntax compilation and `git diff --check` passed. No paid model evaluation, live database test, transactional concurrency test or production acceptance run was performed.

This branch is a review candidate. First resolve Fable’s actual diff review. Then pass the database and failure truth gates and run a bounded model evaluation with approved cost and data. Verify the app renders the new receipt meanings. Only then request production GO and perform the controlled Carlos walkthrough. Broader invitations follow observed success, not a green unit suite.

The changes add JSON fields and require no migration themselves. Older payloads remain readable but are no longer trusted for fresh judgment reuse. Rollback must also disable incompatible receipt reuse; blindly reverting the engine can restore the original stale cache behavior. Record exact revisions, test receipts and deployment status together.
