"""
Patient seeding tests: one patient per metadata patient id, never a duplicate.

    cd backend && ../.venv/Scripts/python.exe -m unittest tests.test_seed_patients -v
"""

from __future__ import annotations

import unittest
from datetime import date

from scripts.seed_patients import plan_patients

DOCS = [
    {"doc_id": "id_PT_1", "patient_id": "PT_1", "policy_number": "MED-GLD-1"},
    {"doc_id": "claim_PT_1", "patient_id": "PT_1", "policy_number": "MED-GLD-1"},
    {"doc_id": "rx_PT_1", "patient_id": "PT_1", "policy_number": "MED-GLD-1"},
    {"doc_id": "id_PT_2", "patient_id": "PT_2", "policy_number": "MED-SLV-2"},
    {"doc_id": "id_PT_3", "patient_id": "PT_3", "policy_number": "MED-SLV-3"},
]
MEMBERS = {
    "PT_1": {"full_name": "Jane Doe", "date_of_birth": date(1980, 2, 14), "policy_number": "OLD"},
    "PT_2": {"full_name": "John Roe", "date_of_birth": None, "policy_number": "MED-SLV-2"},
}


class PlanPatientsTests(unittest.TestCase):
    def test_one_patient_per_patient_id_even_with_many_documents(self):
        create, skipped, missing = plan_patients(DOCS, MEMBERS, set())
        self.assertEqual([p["member_id"] for p in create], ["PT_1", "PT_2"])
        self.assertEqual(skipped, [])
        self.assertEqual(missing, ["PT_3"])

    def test_already_registered_member_is_skipped(self):
        create, skipped, _ = plan_patients(DOCS, MEMBERS, {"PT_1"})
        self.assertEqual([p["member_id"] for p in create], ["PT_2"])
        self.assertEqual(skipped, ["PT_1"])

    def test_second_run_creates_nothing(self):
        registered: set[str] = set()
        first, _, _ = plan_patients(DOCS, MEMBERS, registered)
        second, skipped, _ = plan_patients(DOCS, MEMBERS, registered)
        self.assertEqual(len(first), 2)
        self.assertEqual(second, [])
        self.assertEqual(skipped, ["PT_1", "PT_2"])

    def test_policy_from_metadata_and_name_and_dob_from_member(self):
        create, _, _ = plan_patients(DOCS, MEMBERS, set())
        jane = create[0]
        self.assertEqual(jane["policy_number"], "MED-GLD-1")      # metadata wins over the member row
        self.assertEqual(jane["full_name"], "Jane Doe")
        self.assertEqual(jane["date_of_birth"], "1980-02-14")
        self.assertIsNone(create[1]["date_of_birth"])


if __name__ == "__main__":
    unittest.main()
