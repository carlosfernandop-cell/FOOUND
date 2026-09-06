# Judgment receipt proposal

Local implementation, 6 September 2026. Not adopted product law or deployed schema. Read with `INTELLIGENCE_REVIEW_2026_09_06.md`.

## Additive private payload fields

| Location | Field | Meaning |
| --- | --- | --- |
| Edition | `judgment_basis` | `{format, model, hash}`. Versioned private cache compatibility receipt. Includes person identity, full active Brief, compiled config, actual candidate context and judge configuration in the hash. |
| Seat or refusal | `evidence_hash` | SHA256 of normalized posting title, company, location, URL and actual fetched text. Empty means unknown, never unchanged. |
| Seat or refusal | `research` | Status and original judgment retained alongside a valid revised fit, verdict and research date. |
| Seat or refusal | `deep` | The deeper research output associated with this role. Not proof of source verification. |
| Edition | `deep_role_key` | Which role received the edition’s deeper research result. It may no longer be the lead. |
| Edition | `lead_research_status` | `not_requested`, `applied`, `remembered`, `not_researched_after_promotion` or `unavailable`. |
| Edition intelligence | `deep` | Existing reason enum gains `remembered`. |

Existing row identity, person ownership, chapter structure and picture shape remain unchanged. No database column is added. Original `judged_on` is preserved when a judgment is reused; reading a cached decision does not reset its age.

## App acceptance

Do not attach top level deeper research to the current lead without checking `deep_role_key`. Render a promoted lead’s unresearched status honestly. Preserve original and settled judgment as different stages, not competing unexplained scores. Unknown fields must not crash older readers. An empty result may now have meaningful refusal reasons worth showing.

This proposal does not yet repair degraded judgment output. `engine=heuristic` with `outcome=seats` still exists in the base contract. It must not be presented as model assessment. The coordinated engine and app failure repair is a separate required release gate.

## Reuse and cost

Both full judgment basis and current posting evidence must match. Legacy or malformed receipts are not reused. Explicit reconsideration bypasses reuse. The existing age bound and ordinary read budget remain. First adoption and meaningful context changes can produce additional model reads inside the existing scheduling policy. Priority and reconsider exceptions still require a separate capacity cap review.

## Rollback

Additive fields can remain stored when readers roll back. However reverting to the old reuse algorithm restores its insufficient compatibility check. A safe rollback needs cache reuse disabled or an equivalent compatibility safeguard, not only an older engine binary.
