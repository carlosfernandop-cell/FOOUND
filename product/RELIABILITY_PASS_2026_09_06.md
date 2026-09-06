# Reliability implementation, 6 September 2026

## Current integrated review, v6

This v6 checkpoint supersedes the v5 status below. Fable verified the v5 checksum, applied it against the base and independently ran 210 tests plus 15 subtests. He executed the exact integrated migration 017 harness on disposable PostgreSQL 16, including the historical deadlock reproduction, corrected interleavings, measured waits and zero residue. Astra's separate PostgreSQL sandbox limitation remains unchanged.

Fable's receipt recovery companion is now integrated. A failure to save the work basis is named `receipt_unavailable`, never mistaken for an ambiguous edition write. Recovery remains eligible after one hour and no paid work begins without a saved basis. Receipt failures do not count as paid attempts. Astra added two regressions and repairs: receipt cooldown is enforced from history at execution even without the scheduler's latest job fallback; a failure receipt retains current Brief, engine and date without claiming an actual attempt. The engine suite now passes 219 tests plus 15 subtests. Fable's reciprocal review of these final two changes remains open. Migration SQL is unchanged; harness comments now match the enforced safety policy.

Fable's app revision 3 independently passes Astra's real route fixture for the first failed edition and hook fixture for synchronous status ownership. One reader correction is outstanding: a failed read after an account change must not retain the previous account's edition. See `outputs/FOOUND-app-reliability-review.md` in the outer workspace. The app and engine remain local. No release approval or real model quality acceptance is implied.

## Integrated v5 review history

This section supersedes the implementation checkpoint below. Astra integrated Fable's migration 017 revision 2 and engine companion locally, with stricter parsing that requires HTTP 400, PostgreSQL code P0001 and an exact known authority reason. An arbitrary error string cannot become a successful job completion.

Astra found a lock cycle missed in the first SQL harness: confirming an already active Brief locks Brief then agent while the original trigger locked agent then Brief. Fable reproduced SQLSTATE 40P01 and corrected the protocol to hold only the agent lock. His revised disposable tests cover the original failure, the corrected interleaving, real repeated activation in both orders and an active but unconfirmed Brief. These PostgreSQL results are Fable's evidence. Astra downloaded a separate PostgreSQL 16.2 runtime for independent verification, but initialization failed because the sandbox denies shmget. No server started and no existing database was touched.

Astra additionally hardened the harness to require a safe local target and an entirely empty user schema. A failed preflight query now aborts instead of being treated as an empty database. Five offline shell refusal tests exercise those protections without a database.

Transient assessment or collection failures can retry after one hour, at most three actual attempts for the current Brief and engine. A hard ceiling of six actual attempts per day survives Brief and engine changes. Work receipts are written before the hunt. The policy is checked both at enqueue and execution, including work requested through other doors. The supported hunt, heartbeat and dry run workflows share the existing non cancelling concurrency group. The policy is not an independent database mutex against arbitrary additional workers. Persistence ambiguity and deterministic errors remain held. The normal reading budget has priority and reconsider exceptions, so an attempt cap is not a hard model token or read count cap.

Receipts now include attempt, attempts_today, retry_limit, daily_limit, retry_delay_seconds, retry_policy and retry_after. The app must not promise work merely because a queue row exists, or show a current day failure for historical work. Fable's first app patch has concrete review corrections outstanding: same day Brief replacement, queued versus running, historical date language, cross account stale state and receipt field alignment. No app publication is authorized by this checkpoint.

Current Astra suite: 210 passed plus 15 subtests, with no expected failures. The former pause counterexample now explicitly uses the database guard emulator to test the integration contract. This is not a claim of independently executed PostgreSQL concurrency tests. Syntax and diff checks pass. All changes remain local and need reciprocal v5 review before a production decision.

Status: local implementation for reciprocal review. Not deployed. Carlos authorized this work, not a production migration or invitation send.

## Implemented by Astra

1. Posting evidence uses the same canonical URL as role identity. Tracking parameters no longer cause a new assessment. Identity bearing query parameters remain significant.
2. Private seating receives only valid assessed roles. Failed reads and roles outside the reading budget remain unread, never recommendations or refusals. The public heuristic path is unchanged.
3. The private receipt adds `judgment_status`: `complete`, `incomplete` or `unavailable`. Historical payloads without the field are unknown, not certified complete. Counts and private HTML state how many roles were assessed and remain awaiting assessment.
4. An unavailable result is a local runtime outcome only. The runner records `judgment_unavailable` on the failed job and does not persist an empty edition. There is no new database outcome enum. App handling of this named error still needs reciprocal integration review.
5. Daily recovery checks the active Brief version, including editions with missing historical version data. It does not mistake an older edition for current work.
6. Daily enqueue avoids a known running job and stands down after a failure on the same day, Brief version and engine. A new day, Brief version or engine reopens an attempt. This limits automatic daily retries, not manual or compile initiated jobs. It is not an atomic concurrency guarantee.

New regression cases also verify that remembered research refusals stay refused and that a promoted lead receives its own research on the next hunt.

## Evidence

Astra ran the combined offline suite: 177 passed, one strict expected failure and 15 subtests passed. The remaining expected failure is pause during the hunt before edition persistence. It remains visible until the actual database boundary and runner handling are proved.

## Open before release

Fable owns the isolated database authority proposal and disposable concurrency harness. Astra must review the actual patch. No migration has been applied. Database interleavings, queue recovery across compile and commission, and app presentation of failure need acceptance evidence. Automatic retry limits do not yet cover every entry path.

Incomplete deeper research retry behavior, the narrow legacy research parser completeness case and the discovery prototype v4 remain separate followups. They are not silently included in this patch. No paid model or real market acceptance run occurred.

The approved v10 landing is untouched by this work. Carlos's existing records remain untouched.
