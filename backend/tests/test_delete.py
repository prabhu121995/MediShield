"""
Admin deletes: a claim, one page of a claim, a patient. The database, the claim graph and the
upload folder are stubbed, so these run offline.

    cd backend && ../.venv/Scripts/python.exe -m unittest tests.test_delete -v
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app import auth, config
from app.claim import service
from app.main import app

ADMIN = {"username": "admin1", "role": "admin", "full_name": "Admin One"}
AGENT = {"username": "agent1", "role": "agent", "full_name": "Agent One"}


class DeleteServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.upload_dir = pathlib.Path(self.tmp.name)
        self.files = [self.upload_dir / "p1.png", self.upload_dir / "p2.png"]
        for f in self.files:
            f.write_bytes(b"x")
        self.db = mock.MagicMock()
        self.db.get_bundle.return_value = {"bundle_id": "c1", "title": "C001_PT_1", "status": "APPROVED",
                                           "decision": "APPROVE", "patient_id": "p1"}
        self.db.bundle_documents.return_value = [
            {"case_id": "d1", "file_name": "id.png", "doc_type": "ID_DOCUMENT", "result": {"file_path": str(self.files[0])}},
            {"case_id": "d2", "file_name": "claim.png", "doc_type": "CLAIM_FORM", "result": {"file_path": str(self.files[1])}},
        ]
        self.db.checkpoint_threads.return_value = ["c1:aaaa"]
        self.saver = mock.MagicMock(storage={"c1:bbbb": {}, "other:cccc": {}})
        self.patches = [
            mock.patch.object(service, "db", self.db),
            mock.patch.object(service, "audit", mock.MagicMock()),
            mock.patch.object(service, "get_claim_graph", return_value=mock.MagicMock(checkpointer=self.saver)),
            mock.patch.object(service, "active_claim", return_value=None),
            mock.patch.object(service, "_executor", mock.MagicMock()),
            mock.patch.object(config, "UPLOAD_DIR", self.upload_dir),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_delete_claim_removes_rows_files_and_checkpoints_and_audits(self):
        service.delete_claim("c1", "admin1")
        self.db.delete_bundle.assert_called_once_with("c1")
        self.assertFalse(any(f.exists() for f in self.files))
        deleted = {c.args[0] for c in self.saver.delete_thread.call_args_list}
        self.assertEqual(deleted, {"c1:aaaa", "c1:bbbb"})
        service.audit.record.assert_called_once()
        args, kwargs = service.audit.record.call_args
        self.assertEqual(args[:3], ("c1", "admin1", "claim_deleted"))
        self.assertEqual(kwargs["detail"]["title"], "C001_PT_1")

    def test_running_claim_is_not_deleted(self):
        self.db.get_bundle.return_value = {**self.db.get_bundle.return_value, "status": "PROCESSING"}
        with self.assertRaises(ValueError):
            service.delete_claim("c1", "admin1")
        self.db.delete_bundle.assert_not_called()

    def test_files_outside_the_upload_folder_are_left_alone(self):
        with tempfile.NamedTemporaryFile(delete=False) as outside:
            pass
        try:
            self.db.bundle_documents.return_value = [{"case_id": "d1", "file_name": "x.png", "result": {"file_path": outside.name}}]
            service.delete_claim("c1", "admin1")
            self.assertTrue(pathlib.Path(outside.name).exists())
        finally:
            pathlib.Path(outside.name).unlink(missing_ok=True)


class DeleteApiTests(unittest.TestCase):
    def setUp(self):
        self.db = mock.MagicMock()
        self.db.get_patient.return_value = {"patient_id": "p1", "full_name": "Jane Doe", "member_id": "PT_1"}
        self.db.patient_usage.return_value = {"claims": 0, "pages": 0}
        self.patches = [mock.patch("app.main.db", self.db), mock.patch("app.main.audit", mock.MagicMock()),
                        mock.patch("app.main.claims_service.delete_claim")]
        for p in self.patches:
            p.start()
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        for p in self.patches:
            p.stop()

    def as_user(self, user):
        app.dependency_overrides[auth.current_user] = lambda: user

    def test_agents_cannot_delete(self):
        self.as_user(AGENT)
        self.assertEqual(self.client.delete("/api/claim-bundles/c1").status_code, 403)
        self.assertEqual(self.client.delete("/api/patients/p1").status_code, 403)
        self.db.delete_patient.assert_not_called()

    def test_admin_deletes_claim(self):
        from app import main
        self.as_user(ADMIN)
        self.assertEqual(self.client.delete("/api/claim-bundles/c1").status_code, 204)
        main.claims_service.delete_claim.assert_called_once_with("c1", "admin1")

    def test_running_claim_is_409_and_unknown_is_404(self):
        from app import main
        self.as_user(ADMIN)
        main.claims_service.delete_claim.side_effect = ValueError("still running")
        self.assertEqual(self.client.delete("/api/claim-bundles/c1").status_code, 409)
        main.claims_service.delete_claim.side_effect = KeyError("c1")
        self.assertEqual(self.client.delete("/api/claim-bundles/c1").status_code, 404)

    def test_admin_deletes_unlinked_patient_and_it_is_audited(self):
        from app import main
        self.as_user(ADMIN)
        self.assertEqual(self.client.delete("/api/patients/p1").status_code, 204)
        self.db.delete_patient.assert_called_once_with("p1")
        self.assertEqual(main.audit.record_entity.call_args.args[:4], ("patient", "p1", "admin1", "patient_deleted"))

    def test_patient_with_claims_is_not_deleted(self):
        self.as_user(ADMIN)
        self.db.patient_usage.return_value = {"claims": 2, "pages": 9}
        res = self.client.delete("/api/patients/p1")
        self.assertEqual(res.status_code, 409)
        self.assertIn("2 claim(s)", res.json()["detail"])
        self.db.delete_patient.assert_not_called()


if __name__ == "__main__":
    unittest.main()
