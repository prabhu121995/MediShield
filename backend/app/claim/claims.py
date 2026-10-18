"""
Claims agent: extract only. It collects the claim form's billing facts and the clinical facts
of the other pages, and hands the policy agent a brief (procedures, diagnoses, amount, dates,
clinical notes) to decide coverage on. It compares and judges nothing: missing or illegible
fields, mismatches and every other flag are investigated by the fraud agent.

The first claim form is the claim; any further claim form is kept for the fraud agent's
duplicate check.
"""

from __future__ import annotations

from app import config
from app.claim._node import claim_node
from app.claim.state import ClaimState


def schema_errors(doc: dict, rules: dict) -> list[dict]:
    """Required fields that are empty, and fields the page marks missing. Used by the fraud agent."""
    spec = rules["document_types"].get(doc.get("doc_type"), {})
    fields = doc.get("fields") or {}
    errors = [{"field": f, "code": "MISSING_FIELD", "message": f"{f} is required"}
              for f in spec.get("required", []) if fields.get(f) in (None, "", [])]
    for name in doc.get("fields_marked_missing") or []:
        if not any(e["field"] == name for e in errors):
            errors.append({"field": name, "code": "MARKED_MISSING", "message": f"the page marks {name} as missing"})
    return errors


def _entry(doc: dict) -> dict:
    fields = dict(doc.get("fields") or {})
    if "cpt_codes" in fields:
        fields["cpt_codes"] = [{"code": c, "description": None} for c in fields.get("cpt_codes") or []]
    return {"doc_id": doc["doc_id"], "file_name": doc.get("file_name"), "kind": doc.get("doc_type"),
            "extracted_fields": fields}


def policy_brief(claim: dict | None, clinical: list[dict]) -> dict:
    """What the policy agent needs to decide coverage."""
    f = (claim or {}).get("extracted_fields") or {}
    diagnoses = list(dict.fromkeys([*(f.get("icd10_codes") or []),
                                    *(code for c in clinical for code in c["extracted_fields"].get("icd10_codes") or [])]))
    medications = list(dict.fromkeys(m for c in clinical for m in c["extracted_fields"].get("medications") or []))
    return {
        "cpt_codes": [c["code"] for c in f.get("cpt_codes") or []],
        "icd10_codes": diagnoses,
        "claim_amount": f.get("claim_amount"),
        "service_date": f.get("service_date"),
        "admission_date": f.get("admission_date"),
        "discharge_date": f.get("discharge_date"),
        "policy_number": f.get("policy_number"),
        "medications": medications,
        "clinical_documents": [c["kind"] for c in clinical],
    }


def review_claim(docs: list[dict], rules: dict | None = None) -> dict:
    rules = rules or config.RULES
    medical = set(rules["medical_documents"])
    claims = [_entry(d) for d in docs if d.get("doc_type") == "CLAIM_FORM"]
    clinical = [_entry(d) for d in docs if d.get("doc_type") in medical - {"CLAIM_FORM"}]

    claim = claims[0] if claims else None
    brief = policy_brief(claim, clinical)
    if claim:
        summary = (f"claim {claim['extracted_fields'].get('claim_number') or '?'}: ${brief['claim_amount'] or 0:,.2f}, "
                   f"CPT {', '.join(brief['cpt_codes']) or 'none'}, ICD-10 {', '.join(brief['icd10_codes']) or 'none'}, "
                   f"service {brief['service_date'] or '?'}; {len(clinical)} clinical page(s); sent to policy")
    else:
        summary = f"no claim form; {len(clinical)} clinical page(s) only"
    return {
        "claim": claim,
        "additional_claims": claims[1:],
        "clinical": clinical,
        "policy_brief": brief,
        "confidence": 0.9 if (claim is None or brief["cpt_codes"]) else 0.5,
        "summary": summary,
    }


@claim_node("claims", writes="claims")
def claims_agent(state: ClaimState) -> dict:
    return review_claim(state.get("docs") or [])
