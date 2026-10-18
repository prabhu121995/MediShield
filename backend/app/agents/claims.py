"""
Stage 3b -- structured extraction (runs in parallel with kyc for a claim form).

Routes by document type:
- CLAIM_FORM -> kind "CLAIM": billed amount, ICD-10, CPT, NPI, service date,
  physician signature. Deterministic schema validation decides schema_valid.
- DISCHARGE_SUMMARY / PRESCRIPTION -> kind "CLINICAL": admission, discharge and
  prescription dates and diagnosis. Stored for the fraud history checks.

The LLM only extracts. Every rule is plain code, so a model slip is a visible
validation error. Failures after retries raise, which becomes an ESCALATE, never
a REJECT.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from app.agents._base import node
from app.agents._util import parse_date
from app.llm import chat_json
from app.state import CaseState

ICD10_RE = re.compile(r"^[A-TV-Z][0-9][0-9A-Z](\.?[0-9A-Z]{1,4})?$")
CPT_RE = re.compile(r"^\d{5}$")
NPI_RE = re.compile(r"^\d{10}$")
REQUIRED = ["claim_amount", "icd10_codes", "cpt_codes", "provider_npi",
            "service_date", "physician_signature"]

CLAIM_PROMPT = """You are a medical claims extraction agent. Read the claim text and
return ONLY a JSON object with these keys:
- patient_name: string or null
- claim_amount: total billed amount in USD as a number, or null
- icd10_codes: list of ICD-10 diagnosis code strings
- cpt_codes: list of objects {"code": "5-digit CPT", "description": string or null}
- provider_npi: 10-digit NPI string, or null
- service_date: date of service as YYYY-MM-DD, or null
- physician_signature: the signing physician's name if signed, otherwise null
Do not invent values. Use null when a value is absent."""

CLINICAL_PROMPT = """You are a clinical record extraction agent. Read the document text
and return ONLY a JSON object with these keys (YYYY-MM-DD dates, or null):
- admission_date, discharge_date, prescription_date
- prior_discharge_date: if the document mentions an EARLIER hospital stay, its discharge date, else null
- diagnosis: the main diagnosis as text, or null
Do not invent values."""


def _normalize_cpt(entry: Any) -> dict | None:
    if isinstance(entry, str):
        entry = {"code": entry, "description": None}
    if not isinstance(entry, dict):
        return None
    return {"code": str(entry.get("code", "")).strip(), "description": entry.get("description")}


def validate_claim(raw: dict[str, Any]) -> dict[str, Any]:
    errors: list[dict[str, str]] = []

    def err(field: str, code: str, message: str) -> None:
        errors.append({"field": field, "code": code, "message": message})

    for key in REQUIRED:
        if raw.get(key) in (None, "", []):
            err(key, "MISSING_FIELD", f"{key} is required")

    amount = raw.get("claim_amount")
    if amount not in (None, ""):
        try:
            amount = float(str(amount).replace("$", "").replace(",", ""))
        except ValueError:
            err("claim_amount", "INVALID_FORMAT", f"not numeric: {amount!r}")
            amount = None

    icd = []
    for code in raw.get("icd10_codes") or []:
        clean = str(code).upper().strip()
        if ICD10_RE.match(clean):
            icd.append(clean)
        else:
            err("icd10_codes", "INVALID_FORMAT", f"bad ICD-10 code: {code!r}")

    cpt = []
    for entry in raw.get("cpt_codes") or []:
        norm = _normalize_cpt(entry)
        if norm is None or not CPT_RE.match(norm["code"]):
            err("cpt_codes", "INVALID_FORMAT", f"bad CPT code: {entry!r}")
        else:
            cpt.append(norm)

    npi = raw.get("provider_npi")
    if npi not in (None, "") and not NPI_RE.match(str(npi)):
        err("provider_npi", "INVALID_FORMAT", f"NPI must be 10 digits: {npi!r}")

    service = parse_date(raw.get("service_date"))
    if raw.get("service_date") not in (None, "") and service is None:
        err("service_date", "INVALID_FORMAT", f"unparseable date: {raw.get('service_date')!r}")
    if service and service > date.today():
        err("service_date", "FUTURE_DATE", "service date is in the future")

    extracted = {
        "patient_name": raw.get("patient_name") or None,
        "claim_amount": amount,
        "icd10_codes": icd,
        "cpt_codes": cpt,
        "provider_npi": str(npi) if npi else None,
        "service_date": service.isoformat() if service else None,
        "physician_signature": raw.get("physician_signature") or None,
    }
    return {"extracted_fields": extracted, "schema_valid": not errors, "validation_errors": errors}


def validate_clinical(raw: dict[str, Any]) -> dict[str, Any]:
    errors = []
    out = {"diagnosis": raw.get("diagnosis") or None}
    for key in ("admission_date", "discharge_date", "prescription_date", "prior_discharge_date"):
        value = raw.get(key)
        parsed = parse_date(value)
        if value not in (None, "") and parsed is None:
            errors.append({"field": key, "code": "INVALID_FORMAT", "message": f"unparseable: {value!r}"})
        out[key] = parsed.isoformat() if parsed else None
    return {"extracted_fields": out, "schema_valid": not errors, "validation_errors": errors}


@node("claims", writes="claims")
def claims_agent(state: CaseState) -> dict:
    prep = state.get("preprocessor") or {}
    text = prep.get("raw_text") or ""
    if not text.strip():
        raise ValueError("no text available for extraction")
    doc_type = (state.get("classifier") or {}).get("doc_type")

    if doc_type == "CLAIM_FORM":
        raw = chat_json(CLAIM_PROMPT, text)
        out = validate_claim(raw)
        out["kind"] = "CLAIM"
        out["extracted_fields"]["patient_name"] = out["extracted_fields"]["patient_name"] or \
            (prep.get("fields") or {}).get("patient_name")
        return out

    raw = chat_json(CLINICAL_PROMPT, text)
    out = validate_clinical(raw)
    out["kind"] = "CLINICAL"
    return out
