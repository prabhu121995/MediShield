"""
KYC agent. Runs in parallel with the claims -> policy branch.

1. Main check: the claimant's name is in the member database. The name on the ID (else the
   claim form, else any page) must match a member record (case and middle initials ignored).
   Several members can share a name, so date of birth, then policy number, picks one; if it
   stays ambiguous the name still exists and KYC passes, with no member linked.
   No member of that name -> KYC failed -> REJECT.
2. The insurance card must be valid: if an ID page is an insurance card (rules:
   kyc.insurance_card_keywords), its expiry date must not be before the treatment date
   (today when no treatment date was read), the card must not print an expired status (rules:
   kyc.card_expired_phrases), and the model must not have read it as marked invalid.
   A card that is not valid fails KYC -> REJECT. Other ID pages (driver's licence, passport)
   are not checked here.

No name on any page -> unsure (confidence 0.5) -> ESCALATE. Every other identity question
(a non-card ID's expiry, DOB or policy differences, name differences between pages) is a
fraud tag, investigated by the fraud agent.
"""

from __future__ import annotations

from datetime import date

from app import config
from app.agents._util import parse_date
from app.claim._node import claim_node
from app.claim.fraud import treatment_date
from app.claim.state import ClaimState
from app.db import get_repository

NAME_ORDER = ["ID_DOCUMENT", "CLAIM_FORM"]          # whose name speaks for the claimant, in order


def candidate_names(docs: list[dict]) -> list[tuple[str, str]]:
    ranked = sorted(docs, key=lambda d: NAME_ORDER.index(d.get("doc_type")) if d.get("doc_type") in NAME_ORDER else 99)
    out = []
    for d in ranked:
        name = (d.get("fields") or {}).get("patient_name")
        if name and name not in [n for n, _ in out]:
            out.append((name, d["file_name"]))
    return out


def pick_member(matches: list[dict], docs: list[dict]) -> dict | None:
    if len(matches) <= 1:
        return matches[0] if matches else None
    dobs = {parse_date((d.get("fields") or {}).get("date_of_birth")) for d in docs} - {None}
    by_dob = [m for m in matches if parse_date(m.get("date_of_birth")) in dobs]
    if len(by_dob) == 1:
        return by_dob[0]
    policies = {(d.get("fields") or {}).get("policy_number") for d in docs} - {None}
    by_policy = [m for m in (by_dob or matches) if m.get("policy_number") in policies]
    return by_policy[0] if len(by_policy) == 1 else None


def is_insurance_card(doc: dict, rules: dict) -> bool:
    kind = str((doc.get("fields") or {}).get("id_kind") or "").lower()
    text = (doc.get("raw_text") or "").lower()
    return any(k in kind or k in text for k in rules["insurance_card_keywords"])


def card_invalid(doc: dict, rules: dict, ref: date, ref_source: str) -> str | None:
    """Why the insurance card is not valid, or None if it is."""
    fields = doc.get("fields") or {}
    expiry = parse_date(fields.get("id_expiry_date"))
    if expiry and expiry < ref:
        return f"expired {expiry}, before the {ref_source} ({ref})"
    text = (doc.get("raw_text") or "").upper()
    hit = next((p for p in rules["card_expired_phrases"] if p.upper() in text), None)
    if hit:
        return f"says '{hit}'"
    if fields.get("card_marked_invalid") is True:
        return "is marked not valid" + (f" ('{fields['invalid_text']}')" if fields.get("invalid_text") else "")
    return None


def check(docs: list[dict], repo, rules: dict | None = None) -> dict:
    rules = (rules or config.RULES)["kyc"]
    checks, fail, flags = [], [], []

    names = candidate_names(docs)
    member, matched_name = None, None
    for name, source in names:
        matches = repo.find_members_by_name(name)
        if matches:
            matched_name = name
            member = pick_member(matches, docs)
            detail = (f"'{name}' ({source}) is member {member['member_id']}" if member
                      else f"'{name}' ({source}) matches {len(matches)} members; none could be singled out")
            checks.append({"check": "member_name", "passed": True, "detail": detail})
            if member is None:
                flags.append("NAME_MATCHES_SEVERAL_MEMBERS")
            break
    if names and matched_name is None:
        fail.append("NAME_NOT_A_MEMBER")
        checks.append({"check": "member_name", "passed": False,
                       "detail": "no member named " + " / ".join(f"'{n}'" for n, _ in names)})
    if not names:
        flags.append("NO_NAME_ON_DOCUMENTS")
        checks.append({"check": "member_name", "passed": None, "detail": "no patient name on any page"})

    ref, ref_source = treatment_date(docs)
    ref, ref_source = (ref, ref_source) if ref else (date.today(), "date of checking")
    cards = [d for d in docs if d.get("doc_type") == "ID_DOCUMENT" and is_insurance_card(d, rules)]
    for d in cards:
        invalid = card_invalid(d, rules, ref, ref_source)
        if invalid:
            fail.append("INSURANCE_CARD_NOT_VALID")
            checks.append({"check": "insurance_card", "passed": False, "detail": f"insurance card {d['file_name']} {invalid}"})
        else:
            expiry = (d.get("fields") or {}).get("id_expiry_date")
            checks.append({"check": "insurance_card", "passed": True,
                           "detail": f"{d['file_name']} is valid" + (f" until {expiry}" if expiry else " (no expiry date on the card)")})

    passed = not fail
    confidence = 0.95 if fail else (0.5 if not names else 0.9)
    summary = ("failed: " + ", ".join(dict.fromkeys(fail))) if fail else \
        ("uncertain: no name to check" if not names else
         f"passed: {member['member_id']} ({member['full_name']})" if member else f"passed: '{matched_name}' is a member")
    return {
        "kyc_passed": passed,
        "flags": list(dict.fromkeys(fail + flags)),
        "fail_reasons": [c["detail"] for c in checks if c["passed"] is False],
        "checks": checks,
        "member": member,
        "insurance_cards": [d["file_name"] for d in cards],
        "confidence": confidence,
        "summary": summary,
    }


@claim_node("kyc", writes="kyc")
def kyc_agent(state: ClaimState) -> dict:
    return check(state.get("docs") or [], get_repository())
