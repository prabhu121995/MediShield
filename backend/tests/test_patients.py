"""
Patient module and dashboard API tests. The database, the pipeline and the upload
folder are replaced with in-memory fakes, so these run offline.

    cd backend && ../.venv/Scripts/python.exe -m unittest tests.test_patients -v
"""

from __future__ import annotations

import io
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app import auth
from app.main import app

AGENT = {"username": "agent1", "role": "agent", "full_name": "Agent One"}
OTHER = {"username": "agent2", "role": "agent", "full_name": "Agent Two"}


class FakeDb:
    def __init__(self):
        self.patients: dict[str, dict] = {}
        self.cases: dict[str, dict] = {}
        self.members = {"PT_1": {"member_id": "PT_1", "policy_number": "MED-GLD-1"},
                        "PT_2": {"member_id": "PT_2", "policy_number": "MED-GLD-2"}}

    def get_repository(self):
        return self

    def find_member(self, member_id):
        return self.members.get(member_id)

    def find_patient_by_member(self, member_id, exclude=None):
        for p in self.patients.values():
            if p.get("member_id") == member_id and p["patient_id"] != exclude:
                return p
        return None

    def insert_patient(self, patient_id, data, created_by):
        self.patients[patient_id] = {**data, "patient_id": patient_id,
                                     "created_by": created_by, "updated_by": created_by}

    def update_patient(self, patient_id, data, updated_by):
        self.patients[patient_id] = {**self.patients[patient_id], **data, "updated_by": updated_by}

    def get_patient(self, patient_id):
        return self.patients.get(patient_id)

    def list_patients(self, search=None, limit=200):
        rows = list(self.patients.values())
        if search:
            rows = [r for r in rows if search.lower() in r["full_name"].lower()]
        return rows

    def list_cases(self, decision=None, status=None, patient_id=None, limit=200):
        return [c for c in self.cases.values() if patient_id is None or c.get("patient_id") == patient_id]

    def get_case(self, case_id):
        return self.cases.get(case_id)

    def dashboard_stats(self):
        return {"total": len(self.cases), "patients": len(self.patients)}


class PatientApiTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDb()
        self.enqueued: list[dict] = []

        def fake_enqueue(path, name, doc_type=None, patient_id=None, created_by=None):
            case_id = f"case-{len(self.enqueued) + 1}"
            self.enqueued.append({"name": name, "patient_id": patient_id, "created_by": created_by})
            self.db.cases[case_id] = {"case_id": case_id, "file_name": name,
                                      "patient_id": patient_id, "status": "PROCESSING"}
            return case_id

        self.patches = [
            mock.patch("app.main.db", self.db),
            mock.patch("app.main.enqueue_upload", side_effect=fake_enqueue),
            mock.patch("app.main._save_upload", side_effect=lambda f, allowed, folder: ("/tmp/x", f.filename)),
        ]
        for p in self.patches:
            p.start()
        app.dependency_overrides[auth.current_user] = lambda: AGENT
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        for p in self.patches:
            p.stop()

    def create(self, **overrides):
        body = {"full_name": "Jane Doe", "date_of_birth": "1980-02-14", "email": "jane@example.com",
                "member_id": "PT_1"}
        body.update(overrides)
        return self.client.post("/api/patients", json=body)

    def test_create_records_creator_and_normalises_blanks(self):
        res = self.create(phone="   ", address="")
        self.assertEqual(res.status_code, 201)
        body = res.json()
        self.assertEqual(body["created_by"], "agent1")
        self.assertEqual(body["updated_by"], "agent1")
        self.assertIsNone(body["phone"])
        self.assertIsNone(body["address"])

    def test_member_id_is_mandatory(self):
        self.assertEqual(self.create(member_id="").status_code, 422)
        self.assertEqual(self.create(member_id="   ").status_code, 422)
        res = self.client.post("/api/patients", json={"full_name": "No Id"})
        self.assertEqual(res.status_code, 422)

    def test_member_id_can_belong_to_only_one_patient(self):
        self.assertEqual(self.create().status_code, 201)
        res = self.create(full_name="Someone Else")
        self.assertEqual(res.status_code, 409)

    def test_update_may_keep_its_own_member_id(self):
        pid = self.create().json()["patient_id"]
        res = self.client.put(f"/api/patients/{pid}", json={"full_name": "Jane Doe", "member_id": "PT_1"})
        self.assertEqual(res.status_code, 200)

    def test_invalid_email_rejected(self):
        self.assertEqual(self.create(email="not-an-email").status_code, 422)

    def test_future_birth_date_rejected(self):
        self.assertEqual(self.create(date_of_birth="2999-01-01").status_code, 422)

    def test_unknown_member_id_rejected_and_known_one_fills_policy(self):
        self.assertEqual(self.create(member_id="NOPE").status_code, 422)
        res = self.create(member_id="PT_1")
        self.assertEqual(res.json()["policy_number"], "MED-GLD-1")

    def test_update_keeps_creator_and_sets_updater(self):
        pid = self.create().json()["patient_id"]
        app.dependency_overrides[auth.current_user] = lambda: OTHER
        res = self.client.put(f"/api/patients/{pid}", json={"full_name": "Jane Smith", "member_id": "PT_1"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["full_name"], "Jane Smith")
        self.assertEqual(res.json()["created_by"], "agent1")
        self.assertEqual(res.json()["updated_by"], "agent2")

    def test_unknown_patient_is_404(self):
        self.assertEqual(self.client.get("/api/patients/missing").status_code, 404)
        self.assertEqual(self.client.put("/api/patients/missing", json={"full_name": "Ab"}).status_code, 404)

    def test_search_by_name(self):
        self.create(full_name="Jane Doe", member_id="PT_1")
        self.create(full_name="John Roe", member_id="PT_2")
        names = [p["full_name"] for p in self.client.get("/api/patients?q=jane").json()]
        self.assertEqual(names, ["Jane Doe"])

    def files(self, *names):
        return [("files", (n, io.BytesIO(b"png-bytes"), "image/png")) for n in names]

    def test_multi_part_upload_queues_every_file_for_the_patient(self):
        pid = self.create().json()["patient_id"]
        res = self.client.post(f"/api/patients/{pid}/cases", files=self.files("a.png", "b.jpg", "c.png"))
        self.assertEqual(res.status_code, 202)
        self.assertEqual(len(res.json()), 3)
        self.assertEqual({e["patient_id"] for e in self.enqueued}, {pid})
        self.assertEqual({e["created_by"] for e in self.enqueued}, {"agent1"})

    def test_bad_file_type_rejects_whole_batch(self):
        pid = self.create().json()["patient_id"]
        res = self.client.post(f"/api/patients/{pid}/cases", files=self.files("a.png", "notes.txt"))
        self.assertEqual(res.status_code, 415)
        self.assertEqual(self.enqueued, [])

    def test_batch_size_limit(self):
        pid = self.create().json()["patient_id"]
        names = [f"f{i}.png" for i in range(26)]
        self.assertEqual(self.client.post(f"/api/patients/{pid}/cases", files=self.files(*names)).status_code, 422)

    def test_upload_to_unknown_patient_is_404(self):
        res = self.client.post("/api/patients/missing/cases", files=self.files("a.png"))
        self.assertEqual(res.status_code, 404)

    def test_patient_cases_lists_only_that_patient(self):
        pid = self.create().json()["patient_id"]
        self.client.post(f"/api/patients/{pid}/cases", files=self.files("a.png"))
        self.db.cases["stray"] = {"case_id": "stray", "patient_id": None}
        cases = self.client.get(f"/api/patients/{pid}/cases").json()
        self.assertEqual([c["patient_id"] for c in cases], [pid])

    def test_dashboard_stats_endpoint(self):
        res = self.client.get("/api/dashboard/stats")
        self.assertEqual(res.status_code, 200)
        self.assertIn("total", res.json())


if __name__ == "__main__":
    unittest.main()
