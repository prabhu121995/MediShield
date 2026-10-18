"""
A reviewer's decision sets the claim status: approve -> APPROVED, reject -> REJECTED,
keep escalated -> still NEEDS_REVIEW (the run stays paused at the checkpoint).
The database and graph are stubbed, so this runs offline.

    cd backend && ../.venv/Scripts/python.exe -m unittest tests.test_review_status -v
"""

from __future__ import annotations

import unittest
from unittest import mock

from app import audit
from app.claim import service


class ReviewStatusTests(unittest.TestCase):
    def setUp(self):
        self.updates: list[tuple[str, dict]] = []
        self.resumed: list[dict] = []
        bundle = {"bundle_id": "c1", "status": "NEEDS_REVIEW", "decision": "ESCALATE", "confidence": 0.9,
                  "result": {"thread_id": "t1", "awaiting_human": True}}

        class Graph:
            def invoke(_, command, config):
                self.resumed.append(command.resume)
                return {"review": {"decision": command.resume["decision"]}, "trace": []}
        for patch in (mock.patch.object(service.db, "get_bundle", lambda *a, **k: bundle),
                      mock.patch.object(service.db, "update_bundle", lambda cid, status, **kw: self.updates.append((status, kw))),
                      mock.patch.object(service, "waiting_for_human", lambda t: True),
                      mock.patch.object(service, "get_claim_graph", lambda: Graph())):
            patch.start()
        self.addCleanup(mock.patch.stopall)
        audit.set_sink(lambda e: None)

    def test_approve_sets_approved_and_resumes_the_run(self):
        service.override_decision("c1", "APPROVE", "checked the card", "ops1")
        self.assertEqual(self.updates[-1][0], "APPROVED")
        self.assertEqual(self.updates[-1][1]["decision"], "APPROVE")
        self.assertEqual(self.resumed[0]["decision"], "APPROVE")

    def test_reject_sets_rejected(self):
        service.override_decision("c1", "REJECT", "fraud confirmed", "ops1")
        self.assertEqual(self.updates[-1][0], "REJECTED")

    def test_keep_escalated_stays_in_review_and_does_not_resume(self):
        service.override_decision("c1", "ESCALATE", "need the provider's records", "ops1")
        status, kw = self.updates[-1]
        self.assertEqual(status, "NEEDS_REVIEW")
        self.assertTrue(kw["needs_review"])
        self.assertEqual(kw["override"]["note"], "need the provider's records")
        self.assertEqual(self.resumed, [])                  # still paused at the checkpoint


if __name__ == "__main__":
    unittest.main()
