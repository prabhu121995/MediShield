"""
Stage 3a -- KYC (runs in parallel with claims for a claim form).

Two parts:
1. insurance_validity(): deterministic port of the notebook function in
   agents/notebook/app-v1.ipynb. If the document is an insurance document and
   prints NOT VALID / INVALID / CARD EXPIRED, KYC fails.
   The notebook version wrapped this in an LLM agent. The check itself is plain
   regex, so the agent wrapper is dropped. A non-insurance document (for example
   a driver's licence) is not failed by this check. It falls through to the
   identity checks.
2. Identity checks against the member and kyc tables.

Missing data escalates (low confidence). It does not reject. An OCR miss must
not reject a policyholder.
"""

from __future__ import annotations

import re
from datetime import date

from app.agents._base import node
from app.agents._util import has_identifier, parse_date, resolve_member
from app.db import get_repository
from app.state import CaseState

INSURANCE_KEYWORDS = [
    r"INSURANCE", r"INSURED", r"POLICY\s*(?:NUMBER|NO|#)", r"MEMBER\s*(?:ID|NUMBER|NO|#)",
    r"CLAIM\s*FORM", r"HEALTH\s*PLAN", r"COVERAGE",
]

INVALID_PATTERNS = [
    (r"\bNOT[\s\-]?VALID\b", "NOT_VALID"),
    (r"\bINVALID\b", "INVALID"),
    (r"\bCARD[\s\-]?EXPIRED\b", "CARD_EXPIRED"),
    (r"\bEXPIRED[\s\-]?CARD\b", "EXPIRED_CARD"),
]


def insurance_validity(text: str) -> dict:
    """Port of check_insurance_validity() from agents/notebook/app-v1.ipynb."""
    if not any(re.search(kw, text, re.IGNORECASE) for kw in INSURANCE_KEYWORDS):
        return {"is_insurance": False, "not_valid_found": False,
                "status": "NOT_AN_INSURANCE_DOCUMENT"}
    for pattern, label in INVALID_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return {"is_insurance": True, "not_valid_found": True,
                    "matched_type": label, "matched_text": m.group(0), "status": "expired_id"}
    return {"is_insurance": True, "not_valid_found": False, "status": "valid"}


def _result(passed: bool, flags: list[str], confidence: float) -> dict:
    return {"kyc_passed": passed, "flags": flags, "confidence": confidence}


@node("kyc", writes="kyc")
def kyc_agent(state: CaseState) -> dict:
    prep = state.get("preprocessor") or {}
    fields = prep.get("fields") or {}
    text = prep.get("raw_text") or ""

    validity = insurance_validity(text)
    if validity["not_valid_found"]:
        return _result(False, [validity["matched_type"], f"MATCHED: {validity['matched_text']}"], 0.98)

    flags: list[str] = []
    if not validity["is_insurance"]:
        flags.append("NOT_AN_INSURANCE_DOCUMENT")      # informational, not a failure

    if not has_identifier(fields):
        return _result(False, flags + ["MEMBER_NOT_IDENTIFIED"], 0.5)

    repo = get_repository()
    member = resolve_member(fields, repo)
    if member is None:
        return _result(False, flags + ["MEMBER_NOT_FOUND"], 0.9)
    member_id = member["member_id"]

    doc_dob = parse_date(fields.get("date_of_birth"))
    member_dob = parse_date(member.get("date_of_birth"))
    if doc_dob and member_dob and doc_dob != member_dob:       # unknown on either side is not a mismatch
        flags.append("DOB_MISMATCH")
    doc_policy = fields.get("policy_number")
    if doc_policy and doc_policy != member.get("policy_number"):
        flags.append("POLICY_MISMATCH")

    kyc_row = repo.kyc_record(member_id) or {}
    expiry = parse_date(kyc_row.get("id_expiry_date") or fields.get("id_expiry_date"))
    # The treatment date on the same page is the reference. Fall back to today.
    reference = parse_date(fields.get("service_date")) or date.today()
    if expiry is None:
        flags.append("ID_EXPIRY_NOT_FOUND")
    elif expiry < reference:
        flags.append("ID_EXPIRED")

    if any(f in flags for f in ("DOB_MISMATCH", "POLICY_MISMATCH", "ID_EXPIRED")):
        return _result(False, flags, 0.95)
    return _result(True, flags, 0.9)
