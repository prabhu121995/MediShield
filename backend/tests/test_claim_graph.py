"""
Claim-graph tests. The vision and text models, blur, MySQL and the vector store are stubbed,
and the checkpointer is in memory, so these run offline with no model, keys or database.

    cd backend && ../.venv/Scripts/python.exe -m unittest tests.test_claim_graph -v
"""

from __future__ import annotations

import unittest
from datetime import date
from unittest import mock

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app import audit, config
from app.agents._util import name_tokens
from app.claim import pages as page_agents, policy as policy_agent
from app.claim.graph import build_claim_graph, set_claim_graph
from app.claim.service import run_claim_graph, run_config
from app.claim.vision import classify_prompt, extract_prompt, normalize_fields
from app.db import set_repository
from app.policy_store import set_policy_store
from tests.test_pipeline import InMemoryRepository

POLICY = "MED-GLD-1234567"
MEMBER = {"member_id": "PT_1", "full_name": "Jane Doe", "date_of_birth": date(1980, 2, 14),
          "policy_number": POLICY, "plan": "GOLD"}
EXCLUDED = {"17000"}


class Repo(InMemoryRepository):
    def find_members_by_name(self, full_name):
        return [m for m in self.members if name_tokens(m["full_name"]) == name_tokens(full_name)]


class FakeStore:
    def __init__(self, empty: bool = False):
        self.empty = empty

    def count(self):
        return 0 if self.empty else 50

    def search(self, query, k=4):
        return [{"text": "17000-17999 Destruction of benign lesions (cosmetic) excluded",
                 "source": "medishield_gold_plan.pdf", "score": 0.7}]


def fake_policy_llm(system, user):
    codes = user.split("\n")[0].split(": ")[1].split(", ")
    bad = [c for c in codes if c in EXCLUDED]
    return {"covered": not bad, "coverage_percentage": 0 if bad else 100,
            "policy_clause": "17000-17999 excluded (cosmetic)" if bad else "no exclusion applies",
            "exclusions": [f"{c}: cosmetic" for c in bad]}


def page(doc_type, text="", quality="clear", confidence=0.95, missing=(), **fields):
    return {"doc_type": doc_type, "confidence": confidence, "quality": quality, "text": text,
            "fields": fields, "missing": list(missing)}


def claim(cpt=("99213",), amount=250.0, npi="1234567890", signature="Dr. Alan Smith", service="2025-03-10",
          number="CLM-2024-00099", icd=("E11.9",), admission=None, discharge=None, missing=(), name="Jane Doe Q.",
          dob="1980-02-14", policy=POLICY):
    return page("CLAIM_FORM", "HEALTH INSURANCE CLAIM FORM", missing=missing, patient_name=name, date_of_birth=dob,
                policy_number=policy, claim_number=number, service_date=service, admission_date=admission,
                discharge_date=discharge, claim_amount=amount, icd10_codes=list(icd), cpt_codes=list(cpt),
                provider_npi=npi, physician_signature=signature)


def id_card(expiry="2029-12-31", name="JANE DOE Q.", text="DRIVER'S LICENSE", kind="driver's licence", dob="1980-02-14"):
    return page("ID_DOCUMENT", text, patient_name=name, date_of_birth=dob, policy_number=POLICY,
                id_kind=kind, id_expiry_date=expiry, card_marked_invalid=False)


def discharge(admit="2025-03-10", out="2025-03-12"):
    return page("DISCHARGE_SUMMARY", "DISCHARGE SUMMARY", patient_name="Jane Doe Q.", date_of_birth="1980-02-14",
                policy_number=POLICY, admission_date=admit, discharge_date=out, icd10_codes=["E11.9"], cpt_codes=[])


def rx(rx_date="2025-03-12", missing=(), quality="clear"):
    return page("PRESCRIPTION", "PRESCRIPTION", quality=quality, missing=missing, patient_name="Jane Doe Q.",
                date_of_birth="1980-02-14", policy_number=POLICY, prescription_date=rx_date,
                prescriber="Dr. Alan Smith", prescriber_npi="1234567890", prescriber_signature="Dr. Alan Smith",
                medications=["Amlodipine (Norvasc)"])


UNKNOWN = page("UNKNOWN", "ComEd electric bill", confidence=0.9)


class ClaimGraphTests(unittest.TestCase):
    def setUp(self):
        self.events: list[dict] = []
        audit.set_sink(self.events.append)
        self.repo = Repo(members=[MEMBER])
        set_repository(self.repo)
        set_policy_store(FakeStore())
        self.graph = build_claim_graph(InMemorySaver())
        set_claim_graph(self.graph)
        self.pages: dict[str, dict] = {}
        self.blurry: set[str] = set()
        by_text = lambda text: next(p for p in self.pages.values() if p["_text"] == text)  # noqa: E731

        def transcribe(path):
            p = self.pages[path]
            return {"text": p["_text"], "image_quality": p["quality"], "model": "stub"}

        def classify(path):
            p = self.pages[path]
            return {"doc_type": p["doc_type"], "confidence": p["confidence"], "image_quality": p["quality"], "model": "stub"}

        def extract(text, doc_type):
            p = by_text(text)
            return {"fields": p["fields"], "fields_marked_missing": p["missing"]} if doc_type != "UNKNOWN" else \
                {"fields": {}, "fields_marked_missing": []}

        for patch in (mock.patch.object(page_agents, "transcribe", transcribe),
                      mock.patch.object(page_agents, "classify", classify),
                      mock.patch.object(page_agents, "extract", extract),
                      mock.patch.object(page_agents, "blur_of", lambda path: 10.0 if path in self.blurry else 900.0),
                      mock.patch.object(policy_agent, "chat_json", fake_policy_llm)):
            patch.start()
        self.addCleanup(mock.patch.stopall)
        for undo in (lambda: set_repository(None), lambda: set_policy_store(None),
                     lambda: audit.set_sink(lambda e: None), lambda: set_claim_graph(None)):
            self.addCleanup(undo)

    def run_claim(self, thread="t1", **pages: dict) -> dict:
        documents = []
        for i, (name, content) in enumerate(pages.items()):
            path = f"/tmp/{name}.png"
            self.pages[path] = {**content, "_text": f"{content['text']} [{name}]"}
            documents.append({"doc_id": f"d{i}", "file_path": path, "file_name": f"{name}.png"})
        return run_claim_graph("claim-1", "test", documents, thread_id=thread)

    def decision(self, state: dict) -> str:
        return state["decision"]["decision"]

    # ---- structure ------------------------------------------------------------------------

    def test_clean_claim_is_approved_with_all_agents(self):
        state = self.run_claim(claim=claim(), id=id_card(), discharge=discharge(), rx=rx())
        out = state["decision"]
        self.assertEqual(out["decision"], "APPROVE", out["justification"])
        self.assertEqual(set(out["agent_summaries"]), {"ingest", "kyc", "claims", "policy", "fraud"})
        self.assertTrue(state["kyc"]["kyc_passed"])
        self.assertEqual(state["kyc"]["member"]["member_id"], "PT_1")
        self.assertEqual(state["fraud"]["fraud_score"], 0.0)
        self.assertNotIn("__interrupt__", state)

    def test_each_page_is_preprocessed_classified_extracted_in_turn(self):
        state = self.run_claim(claim=claim(), id=id_card())
        agents = [e["agent"] for e in state["trace"]]
        self.assertEqual(agents[:7], ["preprocess", "classify", "extract"] * 2 + ["plan"])
        self.assertEqual(set(agents[7:9]), {"kyc", "claims"})              # the parallel super-step
        self.assertEqual(agents[-3:], ["policy", "fraud", "decide"])

    def test_preprocessor_text_is_kept_for_the_ui(self):
        state = self.run_claim(claim=claim(), id=id_card())
        self.assertEqual(state["docs"][1]["raw_text"], "DRIVER'S LICENSE [id]")

    def test_every_step_is_audited_with_a_timestamp(self):
        self.run_claim(claim=claim(), id=id_card())
        done = [e["actor"] for e in self.events if e["event"] == "agent_completed"]
        begun = [e["actor"] for e in self.events if e["event"] == "agent_started"]
        for agent, n in (("preprocess", 2), ("classify", 2), ("extract", 2)):
            self.assertEqual(done.count(agent), n)
            self.assertEqual(begun.count(agent), n)
        for agent in ("plan", "kyc", "claims", "policy", "fraud", "decide"):
            self.assertIn(agent, done)
            self.assertIn(agent, begun)
        self.assertTrue(all(e["created_at"] for e in self.events))

    def test_audit_note_says_how_long_each_agent_took_and_what_it_returned(self):
        self.run_claim(claim=claim(), id=id_card())
        done = {(e["actor"], e["detail"].get("file_name")): e for e in self.events if e["event"] == "agent_completed"}
        pre = done[("preprocess", "id.png")]["detail"]["note"]
        self.assertRegex(pre, r"^preprocess on id\.png took .+ and returned: \d+ characters of text; image clear")
        cls = done[("classify", "claim.png")]["detail"]["note"]
        self.assertIn("returned: type CLAIM_FORM at 95% confidence", cls)
        ext = done[("extract", "claim.png")]["detail"]
        self.assertIn("claim_amount", ext["fields_extracted"])
        self.assertIn("field(s) read", ext["note"])
        decide = next(e for e in self.events if e["actor"] == "decide" and e["event"] == "agent_completed")
        self.assertIn("returned: APPROVE at", decide["detail"]["note"])

    # ---- classification -------------------------------------------------------------------

    def test_blurry_page_escalates(self):
        state = self.run_claim(claim=claim(), id=id_card(), rx=rx(quality="blurry"))
        self.assertEqual(self.decision(state), "ESCALATE")
        self.assertTrue(any("blurry" in r for r in state["decision"]["reasons"]))

    def test_low_variance_page_escalates(self):
        self.blurry.add("/tmp/rx.png")
        self.assertEqual(self.decision(self.run_claim(claim=claim(), id=id_card(), rx=rx())), "ESCALATE")

    def test_unclassifiable_page_holds_then_escalates_when_a_person_continues(self):
        state = self.run_claim(thread="u1", claim=claim(), id=id_card(), other={**rx(), "confidence": 0.3})
        self.assertEqual(state["docs"][2]["doc_type"], "UNKNOWN")
        self.assertEqual(state["docs"][2]["classified_as"], "PRESCRIPTION")
        self.assertIn("__interrupt__", state)
        self.assertEqual(self.graph.get_state(run_config("u1")).next, ("ingest_hold",))
        self.assertNotIn("decision", state)                                  # no claim agent ran
        final = self.graph.invoke(Command(resume={"action": "continue", "note": "ok", "reviewer": "ops1"}),
                                  config=run_config("u1"))
        self.assertEqual(self.decision(final), "ESCALATE")
        self.assertEqual(final["hold"]["action"], "continue")

    def test_unknown_document_alone_holds_without_running_agents(self):
        state = self.run_claim(thread="u2", bill=UNKNOWN)
        self.assertIn("__interrupt__", state)
        self.assertEqual(self.graph.get_state(run_config("u2")).tasks[0].interrupts[0].value["pages"][0]["file_name"],
                         "bill.png")
        self.assertNotIn("kyc", [e["agent"] for e in state["trace"]])

    def test_page_that_cannot_be_read_holds_and_a_person_can_stop(self):
        def broken(path):
            raise RuntimeError("LLM call failed after 3 attempt(s): timed out")
        with mock.patch.object(page_agents, "transcribe", broken):
            state = self.run_claim(thread="u3", claim=claim(), id=id_card())
        self.assertTrue(state["docs"][0]["error"].startswith("preprocess failed"))
        self.assertEqual(self.graph.get_state(run_config("u3")).next, ("ingest_hold",))
        skipped = [e for e in self.events if e["actor"] == "classify" and e["event"] == "agent_completed"][0]
        self.assertIn("skipped because an earlier step failed", skipped["detail"]["note"])
        final = self.graph.invoke(Command(resume={"action": "stop", "reviewer": "ops1"}), config=run_config("u3"))
        self.assertEqual(final["hold"]["action"], "stop")
        self.assertNotIn("decision", final)
        self.assertEqual(self.graph.get_state(run_config("u3")).next, ())

    def test_a_stopped_claim_ends_before_the_next_agent(self):
        from app.claim import _node
        _node.set_cancel_check(lambda claim_id: True)
        self.addCleanup(_node.set_cancel_check, lambda claim_id: False)
        with self.assertRaises(_node.ClaimCancelled):
            self.run_claim(thread="s1", claim=claim())

    def test_reviewer_type_correction_is_used(self):
        self.pages["/tmp/x.png"] = {**rx(), "doc_type": "UNKNOWN", "_text": "x"}
        state = run_claim_graph("claim-1", "t", [{"doc_id": "d0", "file_path": "/tmp/x.png", "file_name": "x.png",
                                                  "doc_type_override": "PRESCRIPTION"}], thread_id="t-x")
        self.assertEqual(state["docs"][0]["doc_type"], "PRESCRIPTION")
        self.assertEqual(state["docs"][0]["classifier_source"], "human_override")

    # ---- KYC: member name + insurance card wording only -----------------------------------

    def test_name_not_a_member_rejects(self):
        state = self.run_claim(claim=claim(name="Max Power"), id=id_card(name="MAX POWER"))
        self.assertEqual(self.decision(state), "REJECT")
        self.assertIn("NAME_NOT_A_MEMBER", state["kyc"]["flags"])

    def test_insurance_card_saying_card_expired_rejects(self):
        card = id_card(text="MediShield Health Insurance GOLD PLAN MEMBER ID CARD ** CARD EXPIRED - NOT VALID **",
                       kind="insurance member card")
        state = self.run_claim(claim=claim(), id=card)
        self.assertEqual(self.decision(state), "REJECT")
        self.assertIn("CARD_INVALID", state["kyc"]["flags"])

    def test_not_valid_for_federal_purposes_on_a_state_id_is_fine(self):
        card = id_card(text="STATE OF ILLINOIS IDENTIFICATION CARD NOT VALID FOR FEDERAL PURPOSES", kind="state ID")
        self.assertEqual(self.decision(self.run_claim(claim=claim(), id=card)), "APPROVE")

    def test_shared_name_is_resolved_by_date_of_birth(self):
        self.repo.members.append({**MEMBER, "member_id": "PT_2", "date_of_birth": date(1990, 1, 1), "policy_number": "X"})
        state = self.run_claim(claim=claim(), id=id_card())
        self.assertEqual(state["kyc"]["member"]["member_id"], "PT_1")

    # ---- fraud agent investigates every other identity question ----------------------------

    def test_expired_id_date_is_a_fraud_tag_not_a_kyc_failure(self):
        state = self.run_claim(claim=claim(), id=id_card(expiry="2025-01-31"))
        self.assertTrue(state["kyc"]["kyc_passed"])
        self.assertIn("id_expired", state["fraud"]["tags"])
        self.assertEqual(self.decision(state), "ESCALATE")

    def test_id_expiring_after_treatment_is_still_valid(self):
        state = self.run_claim(claim=claim(service="2025-03-10"), id=id_card(expiry="2025-03-20"))
        self.assertEqual(self.decision(state), "APPROVE", state["decision"]["justification"])

    def test_dob_difference_is_a_fraud_tag(self):
        state = self.run_claim(claim=claim(dob="1981-02-14"), id=id_card())
        self.assertIn("dob_mismatch", state["fraud"]["tags"])

    def test_policy_difference_is_a_fraud_tag(self):
        state = self.run_claim(claim=claim(policy="MED-GLD-9999999"), id=id_card())
        self.assertIn("policy_mismatch", state["fraud"]["tags"])

    def test_name_mismatch_across_documents_escalates(self):
        self.repo.members.append({**MEMBER, "member_id": "PT_3", "full_name": "Joan Dove"})
        state = self.run_claim(claim=claim(), id=id_card(name="JOAN DOVE Q."))
        self.assertIn("name_mismatch", state["fraud"]["tags"])

    def test_duplicate_claim_forms_in_one_claim_escalate(self):
        state = self.run_claim(claim=claim(), claim_dup=claim(), id=id_card())
        self.assertIn("duplicate_claim", state["fraud"]["tags"])

    def test_amount_just_under_review_limit_escalates(self):
        self.assertIn("amount_under_10k", self.run_claim(claim=claim(amount=9875.0), id=id_card())["fraud"]["tags"])

    def test_fraud_parameters_come_from_the_rules(self):
        rules = {**config.RULES, "fraud": {**config.RULES["fraud"], "amount_under_10k": {"severity": 0.6, "low": 200, "high": 300}}}
        with mock.patch.object(config, "RULES", rules):
            self.assertIn("amount_under_10k", self.run_claim(claim=claim(amount=250.0), id=id_card())["fraud"]["tags"])

    def test_procedure_without_matching_diagnosis_escalates(self):
        state = self.run_claim(claim=claim(cpt=("59400",), icd=("K35.80",)), id=id_card())
        self.assertIn("proc_diag_mismatch", state["fraud"]["tags"])

    def test_second_stay_within_30_days_escalates(self):
        state = self.run_claim(claim=claim(admission="2025-02-20", discharge="2025-02-23"), id=id_card(),
                               discharge=discharge(admit="2025-03-10"))
        self.assertIn("readmission_30d", state["fraud"]["tags"])

    def test_prescription_45_days_after_service_escalates(self):
        state = self.run_claim(claim=claim(), id=id_card(), rx=rx(rx_date="2025-05-01"))
        self.assertIn("date_conflict", state["fraud"]["tags"])

    # ---- claims and policy -----------------------------------------------------------------

    def test_excluded_procedure_rejects(self):
        state = self.run_claim(claim=claim(cpt=("17000",)), id=id_card())
        self.assertEqual(self.decision(state), "REJECT")
        self.assertFalse(state["policy"]["covered"])

    def test_required_fields_reject(self):
        state = self.run_claim(claim=claim(npi=None, signature=None), id=id_card())
        self.assertEqual(self.decision(state), "REJECT")
        self.assertTrue(any("provider_npi" in e for e in state["claims"]["schema_errors"]))

    def test_field_the_page_marks_missing_rejects(self):
        state = self.run_claim(claim=claim(), id=id_card(), rx=rx(missing=["sig"]))
        self.assertEqual(self.decision(state), "REJECT")

    def test_policy_store_empty_escalates(self):
        set_policy_store(FakeStore(empty=True))
        self.assertEqual(self.decision(self.run_claim(claim=claim(), id=id_card())), "ESCALATE")

    def test_missing_claim_form_escalates(self):
        state = self.run_claim(id=id_card(), rx=rx())
        self.assertIn("CLAIM_FORM", state["plan"]["missing_required"])
        self.assertEqual(self.decision(state), "ESCALATE")

    # ---- human in the loop -----------------------------------------------------------------

    def test_escalated_claim_pauses_at_a_checkpoint_until_a_person_decides(self):
        state = self.run_claim(thread="hil-1", claim=claim(amount=9875.0), id=id_card())
        self.assertEqual(self.decision(state), "ESCALATE")
        self.assertIn("__interrupt__", state)
        snapshot = self.graph.get_state(run_config("hil-1"))
        self.assertEqual(snapshot.next, ("human_review",))
        self.assertIn("amount_under_10k", snapshot.tasks[0].interrupts[0].value["fraud_tags"])

        final = self.graph.invoke(Command(resume={"decision": "APPROVE", "note": "invoice checked", "reviewer": "ops1"}),
                                  config=run_config("hil-1"))
        self.assertEqual(final["review"]["decision"], "APPROVE")
        self.assertEqual(final["review"]["original_decision"], "ESCALATE")
        self.assertEqual(final["decision"]["decision"], "ESCALATE")          # the agents' decision is kept
        self.assertEqual(self.graph.get_state(run_config("hil-1")).next, ())
        self.assertIn("human_review", [e["actor"] for e in self.events])

    def test_approved_and_rejected_claims_do_not_pause(self):
        for thread, pages in (("a", {"claim": claim(), "id": id_card()}),
                              ("r", {"claim": claim(cpt=("17000",)), "id": id_card()})):
            self.run_claim(thread=thread, **pages)
            self.assertEqual(self.graph.get_state(run_config(thread)).next, ())


class VisionContractTests(unittest.TestCase):
    def test_classify_prompt_lists_every_type(self):
        prompt = classify_prompt(config.RULES)
        for name in config.RULES["document_types"]:
            self.assertIn(name, prompt)

    def test_extract_prompt_asks_for_that_types_fields(self):
        prompt = extract_prompt("CLAIM_FORM", config.RULES)
        for field in config.RULES["document_types"]["CLAIM_FORM"]["fields"]:
            self.assertIn(field, prompt)

    def test_normalize_coerces_declared_types(self):
        out = normalize_fields({"fields": {"claim_amount": "$9,875.00", "service_date": "03/10/2025",
                                           "cpt_codes": "99213, 17000", "physician_signature": "null"}},
                               "CLAIM_FORM", config.RULES)["fields"]
        self.assertEqual((out["claim_amount"], out["service_date"], out["cpt_codes"]), (9875.0, "2025-03-10", ["99213", "17000"]))
        self.assertIsNone(out["physician_signature"])


if __name__ == "__main__":
    unittest.main()
