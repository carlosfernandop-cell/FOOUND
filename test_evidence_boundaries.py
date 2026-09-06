"""Offline evidence, authority and log privacy counterexamples."""
import json
import unittest
from types import SimpleNamespace

import candidate_context as cc
import synthesis_runner as sr


ITEM = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


class EvidenceBoundaries(unittest.TestCase):
    def statement(self, **changes):
        result = {"layer": "self", "statement": "I want to build a product practice.",
                  "provenance": "stated", "evidence": [ITEM], "is_direction": True}
        result.update(changes)
        return json.dumps({"statements": [result]})

    def test_model_cannot_confirm_its_own_statement(self):
        with self.assertRaisesRegex(sr.ValidationError, "provenance_not_allowed"):
            sr.validate_and_map(self.statement(provenance="confirmed"), {ITEM}, [])

    def test_statement_cannot_cite_another_batch(self):
        with self.assertRaisesRegex(sr.ValidationError, "citation_outside_batch"):
            sr.validate_and_map(self.statement(evidence=[OTHER]), {ITEM}, [])

    def test_explicitly_retracted_statement_cannot_be_reasserted(self):
        with self.assertRaisesRegex(sr.ValidationError, "reasserted_retracted"):
            sr.validate_and_map(self.statement(), {ITEM}, [],
                                [{"statement": "I want to build a product practice."}])

    def test_unconfirmed_or_inactive_memory_never_enters_candidate_context(self):
        rows = [{"id": "fact", "layer": "self", "statement": "I build teams.",
                 "status": "active", "provenance": "confirmed"}]
        for state, provenance in (("active", "inferred"), ("retracted", "confirmed"),
                                  ("tension", "confirmed"), ("superseded", "confirmed")):
            rows.append(dict(rows[0], id=state + provenance, status=state,
                             provenance=provenance, statement="This must not enter."))
        context = cc.compile_candidate_context(rows=rows)
        self.assertEqual(context["statements"], 1)
        self.assertNotIn("This must not enter.", context["text"])

    def test_unexpected_failure_does_not_print_evidence_in_public_logs(self):
        def fail_read(ids):
            raise ValueError("PRIVATE_EVIDENCE_SENTINEL")
        settlements = []
        db = SimpleNamespace(
            stale_running_synthesize_jobs=lambda minutes: [],
            oldest_queued_synthesize_job=lambda: {"id": "job-fixture", "agent_id": "agent-fixture"},
            claim=lambda job: {"status": "claimed", "items": [ITEM]},
            evidence_rows=fail_read,
            finalize_failed=lambda job, error: settlements.append((job, error)),
        )
        with self.assertLogs(sr.log, level="INFO") as logs:
            report = sr.Runner(db, storage=None, model=None).run_once()
        self.assertEqual(report.action, "aborted")
        self.assertEqual(settlements, [("job-fixture", None)])
        self.assertNotIn("PRIVATE_EVIDENCE_SENTINEL", "\n".join(logs.output))
        self.assertIn("ValueError", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
