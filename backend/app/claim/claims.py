"""
Claims agent: the claim form's billing facts and the clinical facts of the other pages.

Schema rules come from pipeline_rules.json: a document type's `required` fields must be
present, and any field the page itself marks missing or illegible (the vision model reports
these as fields_marked_missing) is an error. The first claim form is the claim; any further
claim form is kept for the fraud agent's duplicate check. Policy runs next on its CPT codes.
"""

from __future__ import annotations

from app import config
from app.claim._node import claim_node
from app.claim.state import ClaimState


def schema_errors(doc: dict, rules: dict) -> list[dict]:
    spec = rules["document_types"].get(doc.get("doc_type"), {})
    fields = doc.get("fields") or {}
    errors = [{"field": f, "code": "MISSING_FIELD", "message": f"{f} is required"}
              for f in spec.get("required", []) if fields.get(f) in (None, "", [])]
    for name in doc.get("fields_marked_missing") or []:
        if not any(e["field"] == name for e in errors):
            errors.append({"field": name, "code": "MARKED_MISSING", "message": f"the page marks {name} as missing"})
    return errors


def _entry(doc: dict, rules: dict, validate: bool) -> dict:
    errors = schema_errors(doc, rules) if validate else []
    fields = dict(doc.get("fields") or {})
    if "cpt_codes" in fields:
        fields["cpt_codes"] = [{"code": c, "description": None} for c in fields.get("cpt_codes") or []]
    return {"doc_id": doc["doc_id"], "file_name": doc.get("file_name"), "kind": doc.get("doc_type"),
            "extracted_fields": fields, "schema_valid": not errors, "validation_errors": errors}


def review_claim(docs: list[dict], rules: dict | None = None) -> dict:
    rules = rules or config.RULES
    medical = set(rules["medical_documents"])
    validated = {t for t, spec in rules["document_types"].items() if spec.get("required")}
    claims = [_entry(d, rules, True) for d in docs if d.get("doc_type") == "CLAIM_FORM"]
    clinical = [_entry(d, rules, d.get("doc_type") in validated) for d in docs
                if d.get("doc_type") in medical - {"CLAIM_FORM"}]

    claim = claims[0] if claims else None
    checked = ([claim] if claim else []) + clinical
    errors = [f"{c['file_name']}: {e['code']} {e['field']}" for c in checked for e in c["validation_errors"]]
    if claim:
        f = claim["extracted_fields"]
        summary = (f"claim {f.get('claim_number') or '?'}: ${f.get('claim_amount') or 0:,.2f}, "
                   f"CPT {', '.join(c['code'] for c in f.get('cpt_codes') or []) or 'none'}, "
                   f"service {f.get('service_date') or '?'}")
    else:
        summary = "no claim form; clinical facts only"
    if errors:
        summary += f"; {len(errors)} schema error(s)"
    return {
        "claim": claim,
        "additional_claims": claims[1:],
        "clinical": clinical,
        "schema_valid": not errors,
        "schema_errors": errors,
        "confidence": 0.9 if (claim is None or claim["extracted_fields"].get("cpt_codes")) else 0.5,
        "summary": summary,
    }


@claim_node("claims", writes="claims")
def claims_agent(state: ClaimState) -> dict:
    return review_claim(state.get("docs") or [])
