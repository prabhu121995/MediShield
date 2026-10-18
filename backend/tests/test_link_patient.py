"""
A claim is linked to the member its pages name, whichever page or field carries the ID:
pages often print the policy number in the member_id field. Then name + date of birth,
then the member KYC picked. The database is stubbed, so this runs offline.

    cd backend && ../.venv/Scripts/python.exe -m unittest tests.test_link_patient -v
"""

from __future__ import annotations

import unittest
from unittest import mock

from app.claim import intake

MEMBER = {"member_id": "PT_19116", "full_name": "Christopher Harris I.", "policy_number": "MED-GLD-6770619",
          "date_of_birth": "1954-04-25"}


class Repo:
    def find_member(self, member_id):
        return MEMBER if member_id == MEMBER["member_id"] else None

    def find_member_by_policy(self, policy):
        return MEMBER if policy == MEMBER["policy_number"] else None

    def find_member_by_name_dob(self, name, dob):
        return MEMBER if name.lower() == MEMBER["full_name"].lower() and dob == MEMBER["date_of_birth"] else None


def page(file_name, **fields):
    return {"file_name": file_name, "fields": fields}


class IdentifyMemberTests(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(intake.db, "get_repository", lambda: Repo())
        patch.start()
        self.addCleanup(patch.stop)

    def test_policy_number_printed_as_member_id_on_a_non_id_page(self):
        member, how = intake.identify_member([page("rx.png", member_id="med-gld-6770619 ")])
        self.assertEqual(member["member_id"], "PT_19116")
        self.assertIn("rx.png", how)

    def test_member_id_on_any_page(self):
        member, _ = intake.identify_member([page("a.png"), page("discharge.png", member_id="PT_19116")])
        self.assertEqual(member["member_id"], "PT_19116")

    def test_name_and_date_of_birth_when_no_id_matches(self):
        member, how = intake.identify_member([page("c.png", member_id="X1", patient_name="CHRISTOPHER HARRIS I.",
                                                   date_of_birth="1954-04-25")])
        self.assertEqual(member["member_id"], "PT_19116")
        self.assertIn("date of birth", how)

    def test_kyc_member_is_the_last_resort(self):
        self.assertEqual(intake.identify_member([page("c.png")], MEMBER)[1], "KYC name match")
        self.assertEqual(intake.identify_member([page("c.png")]), (None, None))


if __name__ == "__main__":
    unittest.main()
