"""
Duplicate checkpoint: a claim the patient already filed goes straight to a person.

Not an agent: a lookup right after plan, before any agent runs. It reads only what the
pages already gave (the claim form's service date and amount; the member from the member
ID / policy number, or name + date of birth, printed on the pages) and the member's stored
claims. When a stored claim of the same member has the same date of service and the same
billed amount, the claim is a duplicate: kyc, claims, policy and fraud are skipped (no model
calls), decide escalates it and the run pauses at human_review (rules: "duplicate_review").

A patient may have many claims; only an exact repeat (same DOS and amount) is sent to review.
Two claim forms inside one upload are left to the fraud agent's duplicate_claim tag.
"""

from __future__ import annotations

from app import config
from app.agents._util import parse_date
from app.claim import intake
from app.claim._node import claim_node
from app.claim.state import ClaimState
from app.db import get_repository


def _amount(value) -> float | None:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def same_bill(fields: dict, prior: dict) -> bool:
    service, amount = parse_date(fields.get("service_date")), _amount(fields.get("claim_amount"))
    return bool(service and amount is not None
                and parse_date(prior.get("service_date")) == service and _amount(prior.get("claim_amount")) == amount)


def find_duplicate(state: ClaimState, repo) -> dict:
    docs = state.get("docs") or []
    claim = next((d for d in docs if d.get("doc_type") == "CLAIM_FORM" and not d.get("error")), None)
    out = {"is_duplicate": False, "duplicate_of": None, "member_id": None,
           "service_date": None, "claim_amount": None, "checked": 0, "confidence": 1.0}
    if not config.RULES.get("duplicate_review", True):
        return {**out, "summary": "not checked: duplicate review is off (rules: duplicate_review)"}
    if not claim:
        return {**out, "summary": "not checked: no claim form"}
    f = claim.get("fields") or {}
    out.update(service_date=f.get("service_date"), claim_amount=f.get("claim_amount"))
    if not parse_date(f.get("service_date")) or _amount(f.get("claim_amount")) is None:
        return {**out, "summary": "not checked: the claim form has no service date or amount"}
    member, found_by = intake.identify_member(docs)
    if not member:
        return {**out, "summary": "not checked: no known member on the pages"}
    out["member_id"] = member["member_id"]
    history = repo.claims_for_member(member["member_id"], state.get("claim_id"))
    out["checked"] = len(history)
    prior = next((p for p in history if same_bill(f, p)), None)
    if prior is None:
        return {**out, "summary": f"no duplicate among {len(history)} earlier claim(s) of {member['member_id']} "
                                  f"(member from {found_by})"}
    return {**out, "is_duplicate": True, "duplicate_of": prior["case_id"],
            "summary": (f"duplicate of claim {prior['case_id']}: same date of service {f.get('service_date')} "
                        f"and amount ${_amount(f.get('claim_amount')):,.2f} for {member['member_id']}; "
                        "agents skipped, needs human review")}


@claim_node("duplicate_check", writes="duplicate")
def duplicate_check(state: ClaimState) -> dict:
    return find_duplicate(state, get_repository())
