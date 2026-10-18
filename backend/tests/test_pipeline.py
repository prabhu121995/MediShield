"""
Backend tests. Vision, LLM, vector store and MySQL are all stubbed, so these run
offline and need no keys or database.

    cd backend && ../.venv/Scripts/python.exe -m unittest tests.test_pipeline -v
"""

from __future__ import annotations

import unittest
from datetime import date
from unittest import mock

from app.agents.classifier import classify_text
from app.agents.claims import validate_claim
from app.agents.fraud import detect, _name_tokens
from app.agents.kyc import insurance_validity
from app.agents.policy import PolicyDecision, decide_coverage
from app.db import set_repository
from app.policy_store import set_policy_store
from app.service import run_pipeline

CLAIM_TEXT = """CMS-1500 CLAIM FORM
Patient Name: Jane Doe
Policy Number: MED-GLD-1234567
Date of Birth: 1980-02-14
Date of Service: 2025-03-10
Diagnosis: E11.9
CPT 99213 Office visit
Total Charge $250.00
Provider NPI 1234567890
Physician Signature: Dr. A Smith"""

ID_CARD_TEXT = """MediShield Health Insurance Ltd.
Member Name: Jane Doe
Policy Number: MED-GLD-1234567
Date of Birth: 1980-02-14
Expiration Date: 12/31/2027
** CARD EXPIRED ** NOT VALID"""

LLM_CLAIM = {
    "patient_name": "Jane Doe", "claim_amount": 250,
    "icd10_codes": ["E11.9"], "cpt_codes": [{"code": "99213", "description": "Office visit"}],
    "provider_npi": "1234567890", "service_date": "2025-03-10",
    "physician_signature": "Dr. A Smith",
}

FIELDS = {"patient_name": "Jane Doe", "policy_number": "MED-GLD-1234567",
          "date_of_birth": "1980-02-14", "service_date": "2025-03-10"}

MEMBER = {"member_id": "PT_1", "full_name": "Jane Doe",
          "date_of_birth": date(1980, 2, 14), "policy_number": "MED-GLD-1234567"}


class InMemoryRepository:
    def __init__(self, members=(), kyc=(), claims=(), events=(), down=False):
        self.members = list(members)
        self.kyc_rows = list(kyc)
        self.claims_rows = list(claims)
        self.events_rows = list(events)
        self.down = down

    def _check(self):
        if self.down:
            raise ConnectionError("could not connect to MySQL")

    def find_member(self, member_id):
        self._check()
        return next((m for m in self.members if m["member_id"] == member_id), None)

    def find_member_by_policy(self, policy_number):
        self._check()
        return next((m for m in self.members if m["policy_number"] == policy_number), None)

    def find_member_by_name_dob(self, full_name, dob):
        self._check()
        hits = [m for m in self.members if m["full_name"].lower() == full_name.lower()
                and m["date_of_birth"] == dob]
        return hits[0] if len(hits) == 1 else None

    def kyc_record(self, member_id):
        self._check()
        return next((k for k in self.kyc_rows if k["member_id"] == member_id), None)

    def claims_for_member(self, member_id, exclude_case):
        self._check()
        return [c for c in self.claims_rows if c["member_id"] == member_id
                and c["case_id"] != exclude_case]

    def clinical_events_for_member(self, member_id, exclude_case):
        self._check()
        return [e for e in self.events_rows if e["member_id"] == member_id
                and e["case_id"] != exclude_case]


class FakePolicyStore:
    def __init__(self, hits=None):
        self.hits = hits if hits is not None else [
            {"text": "17000-17999 Cosmetic destruction of lesions is excluded.",
             "source": "gold.pdf", "score": 0.6}]

    def count(self):
        return len(self.hits)

    def search(self, query, k=4):
        return self.hits


def _run(text: str, llm=None, repo=None, blur=500.0, store=None, doc_override=None,
         fields=None, ocr_error=None, policy=None):
    set_repository(repo or InMemoryRepository(members=[MEMBER],
                                              kyc=[{"member_id": "PT_1", "id_expiry_date": date(2028, 1, 1)}]))
    set_policy_store(store or FakePolicyStore())
    read = mock.patch("app.agents.preprocessor.read_document",
                      side_effect=ocr_error) if ocr_error else mock.patch(
        "app.agents.preprocessor.read_document",
        return_value={"raw_text": text, "fields": fields if fields is not None else FIELDS})
    with read, mock.patch("app.agents.preprocessor.blur_score", return_value=blur), \
         mock.patch("app.agents.claims.chat_json", return_value=llm if llm is not None else LLM_CLAIM), \
         mock.patch("app.agents.policy.chat_json", return_value=policy or {
                    "covered": True, "coverage_percentage": 100.0,
                    "policy_clause": "99213 is covered.", "exclusions": []}):
        return run_pipeline("dummy/doc.png", "doc.png", case_id="case-1", override=doc_override)


class ClassifierTests(unittest.TestCase):
    def test_claim_form(self):
        self.assertEqual(classify_text(CLAIM_TEXT)["doc_type"], "CLAIM_FORM")

    def test_insurance_card_is_id_document(self):
        self.assertEqual(classify_text(ID_CARD_TEXT)["doc_type"], "ID_DOCUMENT")

    def test_discharge_report_title(self):
        t = "OSF Saint Francis Medical Center PATIENT DISCHARGE REPORT Admission Date 01/02/2025"
        self.assertEqual(classify_text(t)["doc_type"], "DISCHARGE_SUMMARY")

    def test_no_markers_is_unknown(self):
        self.assertEqual(classify_text("lorem ipsum")["doc_type"], "UNKNOWN")


class InsuranceValidityTests(unittest.TestCase):
    """Port of check_insurance_validity() in agents/notebook/app-v1.ipynb."""

    def test_card_expired_marking_fails(self):
        out = insurance_validity("MEDISHIELD HEALTH INSURANCE  ** CARD EXPIRED **")
        self.assertTrue(out["not_valid_found"])
        self.assertEqual(out["matched_type"], "CARD_EXPIRED")

    def test_not_valid_hyphenated(self):
        out = insurance_validity("MEDISHIELD INSURANCE CARD  NOT-VALID")
        self.assertEqual(out["matched_type"], "NOT_VALID")

    def test_valid_insurance_document_passes(self):
        out = insurance_validity("MEDISHIELD HEALTH INSURANCE  Policy Number MED-GLD-1")
        self.assertTrue(out["is_insurance"])
        self.assertFalse(out["not_valid_found"])

    def test_drivers_licence_is_not_insurance_and_not_failed_by_this_check(self):
        out = insurance_validity("DRIVER LICENSE  EXP 2028-01-01")
        self.assertFalse(out["is_insurance"])
        self.assertFalse(out["not_valid_found"])


class ClaimValidationTests(unittest.TestCase):
    def test_valid_claim(self):
        out = validate_claim(dict(LLM_CLAIM))
        self.assertTrue(out["schema_valid"])
        self.assertEqual(out["extracted_fields"]["cpt_codes"][0]["code"], "99213")

    def test_missing_signature_invalid(self):
        out = validate_claim({**LLM_CLAIM, "physician_signature": None})
        self.assertFalse(out["schema_valid"])
        self.assertEqual(out["validation_errors"][0]["field"], "physician_signature")

    def test_bad_npi_and_cpt(self):
        out = validate_claim({**LLM_CLAIM, "provider_npi": "123", "cpt_codes": ["9921"]})
        self.assertEqual({e["field"] for e in out["validation_errors"]}, {"provider_npi", "cpt_codes"})


class PolicyDecisionTests(unittest.TestCase):
    def test_return_type_is_policy_decision(self):
        with mock.patch("app.agents.policy.chat_json", return_value={
                "covered": False, "coverage_percentage": 0, "policy_clause": "17000 excluded",
                "exclusions": "17000: cosmetic"}):
            result = decide_coverage(["17000"], FakePolicyStore())
        self.assertIsInstance(result, PolicyDecision)
        self.assertFalse(result.covered)
        self.assertEqual(result.exclusions, ["17000: cosmetic"])     # string coerced to list
        self.assertEqual(result.cpt_codes_checked, ["17000"])

    def test_no_policy_ingested_raises(self):
        with self.assertRaises(RuntimeError):
            decide_coverage(["99213"], FakePolicyStore(hits=[]))


class FraudTests(unittest.TestCase):
    def claim(self, **kw):
        base = {"claim_amount": 250.0, "service_date": "2025-03-10", "cpt_codes": [],
                "icd10_codes": [], "patient_name": "Jane Doe"}
        return {**base, **kw}

    def test_duplicate_claim(self):
        prior = [{"case_id": "old", "claim_amount": 250.0, "service_date": date(2025, 3, 10)}]
        kinds = [a["type"] for a in detect(self.claim(), None, "Jane Doe", prior, [])]
        self.assertEqual(kinds, ["duplicate_claim"])

    def test_structuring(self):
        kinds = [a["type"] for a in detect(self.claim(claim_amount=9875.0), None, None, [], [])]
        self.assertEqual(kinds, ["amount_under_10k"])

    def test_maternity_without_obstetric_diagnosis(self):
        c = self.claim(cpt_codes=[{"code": "59400"}], icd10_codes=["E11.9"])
        self.assertEqual([a["type"] for a in detect(c, None, None, [], [])], ["proc_diag_mismatch"])

    def test_name_mismatch_ignores_middle_initial(self):
        self.assertEqual(_name_tokens("Sarah White Q."), _name_tokens("Sarah White"))
        kinds = [a["type"] for a in detect(self.claim(patient_name="Mark Lee"), None, "Jane Doe", [], [])]
        self.assertEqual(kinds, ["name_mismatch"])

    def test_readmission_within_30_days(self):
        clinical = {"admission_date": "2025-03-20", "discharge_date": "2025-03-25"}
        prior = [{"case_id": "old", "admission_date": "2025-02-01", "discharge_date": "2025-03-01"}]
        kinds = [a["type"] for a in detect(None, clinical, None, [], prior)]
        self.assertEqual(kinds, ["readmission_30d"])

    def test_readmission_from_prior_stay_on_same_document(self):
        clinical = {"admission_date": "2025-03-20", "discharge_date": "2025-03-25",
                    "prior_discharge_date": "2025-03-01"}
        kinds = [a["type"] for a in detect(None, clinical, None, [], [])]
        self.assertEqual(kinds, ["readmission_30d"])

    def test_prescription_date_conflict_45_days(self):
        clinical = {"admission_date": "2025-01-01", "discharge_date": "2025-01-05",
                    "prescription_date": "2025-02-25"}
        kinds = [a["type"] for a in detect(None, clinical, None, [], [])]
        self.assertEqual(kinds, ["date_conflict"])


class GraphTests(unittest.TestCase):
    def test_clean_claim_is_approved_and_kyc_and_claims_both_ran(self):
        final = _run(CLAIM_TEXT)
        self.assertEqual(final["orchestrator"]["decision"], "APPROVE")
        self.assertEqual(final["kyc"]["kyc_passed"], True)
        self.assertEqual(final["policy"]["covered"], True)
        agents = {e["agent"] for e in final["trace"]}
        self.assertTrue({"kyc", "claims", "policy", "fraud"} <= agents)

    def test_uncovered_cpt_rejected(self):
        final = _run(CLAIM_TEXT, llm={**LLM_CLAIM, "cpt_codes": [{"code": "17000"}]}, policy={
            "covered": False, "coverage_percentage": 0, "policy_clause": "excluded",
            "exclusions": ["17000 cosmetic"]})
        self.assertEqual(final["orchestrator"]["decision"], "REJECT")

    def test_card_marked_not_valid_is_rejected(self):
        final = _run("MEDISHIELD HEALTH INSURANCE Member Name: Jane Doe ** CARD EXPIRED **",
                     fields={"policy_number": "MED-GLD-1234567", "patient_name": "Jane Doe"})
        self.assertEqual(final["orchestrator"]["decision"], "REJECT")
        self.assertIn("CARD_EXPIRED", final["orchestrator"]["justification"])

    def test_expired_id_rejected_using_treatment_date(self):
        repo = InMemoryRepository(members=[MEMBER],
                                  kyc=[{"member_id": "PT_1", "id_expiry_date": date(2025, 1, 31)}])
        final = _run(CLAIM_TEXT, repo=repo)
        self.assertEqual(final["orchestrator"]["decision"], "REJECT")
        self.assertIn("ID_EXPIRED", final["orchestrator"]["justification"])

    def test_blurry_scan_escalates_for_review(self):
        final = _run(CLAIM_TEXT, blur=12.0)
        self.assertEqual(final["orchestrator"]["decision"], "ESCALATE")
        self.assertTrue(final["orchestrator"]["needs_review"])
        self.assertIn("blurry", final["orchestrator"]["justification"])

    def test_duplicate_claim_escalates(self):
        repo = InMemoryRepository(
            members=[MEMBER],
            kyc=[{"member_id": "PT_1", "id_expiry_date": date(2028, 1, 1)}],
            claims=[{"case_id": "old", "member_id": "PT_1", "claim_amount": 250.0,
                     "service_date": date(2025, 3, 10), "cpt_codes": ["99213"], "icd10_codes": []}])
        final = _run(CLAIM_TEXT, repo=repo)
        self.assertEqual(final["orchestrator"]["decision"], "ESCALATE")
        self.assertIn("duplicate_claim", final["orchestrator"]["justification"])

    def test_policy_store_empty_escalates_not_rejects(self):
        final = _run(CLAIM_TEXT, store=FakePolicyStore(hits=[]))
        self.assertEqual(final["orchestrator"]["decision"], "ESCALATE")
        self.assertIn("policy", final["orchestrator"]["justification"])

    def test_database_outage_escalates_not_rejects(self):
        final = _run(CLAIM_TEXT, repo=InMemoryRepository(down=True))
        self.assertEqual(final["orchestrator"]["decision"], "ESCALATE")

    def test_vision_failure_escalates(self):
        final = _run("", ocr_error=RuntimeError("vision 503"))
        self.assertEqual(final["orchestrator"]["decision"], "ESCALATE")
        self.assertIn("vision 503", final["orchestrator"]["justification"])

    def test_unknown_document_escalates(self):
        final = _run("nothing recognisable here")
        self.assertEqual(final["orchestrator"]["decision"], "ESCALATE")

    def test_human_override_reroutes_the_case(self):
        # Text looks like a claim form, but a reviewer says it is an ID document.
        final = _run(CLAIM_TEXT, doc_override="ID_DOCUMENT")
        self.assertEqual(final["classifier"]["doc_type"], "ID_DOCUMENT")
        self.assertNotIn("claims", final)
        self.assertEqual(final["orchestrator"]["decision"], "APPROVE")


class BackgroundJobTests(unittest.TestCase):
    def test_pipeline_crash_ends_as_failed_with_reason(self):
        from app import service
        saved = []
        with mock.patch.object(service, "run_pipeline", side_effect=RuntimeError("quota exhausted")),              mock.patch.object(service.db, "save_case", side_effect=lambda rec: saved.append(rec)),              mock.patch.object(service, "_persist_facts"):
            service._run_job("job-1", "doc.png", "/tmp/doc.png", None)
        final = saved[-1]
        self.assertEqual(final["status"], "FAILED")
        self.assertIn("quota exhausted", final["result"]["error"])
        self.assertIsNone(final["decision"])


if __name__ == "__main__":
    unittest.main()
