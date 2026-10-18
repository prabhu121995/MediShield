"""
Stage 5 -- fraud signals, following dataset_summary.md section 3.

Detector                 Needs                                      Severity
duplicate_claim          claim history: same service date + amount    0.9
amount_under_10k         billed amount in [9000, 10000)               0.6
proc_diag_mismatch       CPT 59400 (maternity) with no O-chapter ICD  0.5
name_mismatch            claim patient name vs member record          0.7
readmission_30d          admission within 30 days of a prior discharge 0.7
date_conflict            prescription date >= 45 days from the service
                         or discharge date on the same document       0.6

Score = 1 - product(1 - severity). Independent signals compound.
A document whose member cannot be identified scores 0 and says so. KYC already
rejects an unknown member, and that cause should be the one reported.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from app.agents._base import node
from app.agents._util import parse_date, resolve_member
from app.db import get_repository
from app.state import CaseState

from app import config

_F = config.RULES["fraud"]                       # same parameters as the claim pipeline (pipeline_rules.json)
STRUCTURING_LOW, STRUCTURING_HIGH = float(_F["amount_under_10k"]["low"]), float(_F["amount_under_10k"]["high"])
READMIT_DAYS = _F["readmission_30d"]["days"]
RX_GAP_DAYS = _F["date_conflict"]["days"]
MATERNITY_CPT = "59400"


def _anomaly(kind: str, severity: float, detail: str) -> dict:
    return {"type": kind, "severity": severity, "detail": detail}


def _name_tokens(name: str) -> set[str]:
    words = re.sub(r"[^a-z ]", " ", name.lower()).split()
    return {w for w in words if len(w) > 1}          # drop middle initials like "Q"


def detect(claim: dict | None, clinical: dict | None, member_name: str | None,
           claim_history: list[dict], clinical_history: list[dict]) -> list[dict]:
    anomalies: list[dict] = []

    if claim:
        amount = claim.get("claim_amount")
        service = parse_date(claim.get("service_date"))
        codes = {c["code"] for c in claim.get("cpt_codes") or []}
        icd = claim.get("icd10_codes") or []

        for prior in claim_history:
            if parse_date(prior.get("service_date")) == service and prior.get("claim_amount") == amount:
                anomalies.append(_anomaly("duplicate_claim", 0.9,
                    f"same service date and amount as prior case {prior['case_id']}"))
                break

        if amount is not None and STRUCTURING_LOW <= amount < STRUCTURING_HIGH:
            anomalies.append(_anomaly("amount_under_10k", 0.6,
                f"billed ${amount:,.2f}, just under the $10,000 review threshold"))

        if MATERNITY_CPT in codes and not any(c.startswith("O") for c in icd):
            anomalies.append(_anomaly("proc_diag_mismatch", 0.5,
                "maternity CPT 59400 billed without an obstetric (O-chapter) diagnosis"))

        doc_name = claim.get("patient_name")
        if doc_name and member_name and _name_tokens(doc_name) != _name_tokens(member_name):
            anomalies.append(_anomaly("name_mismatch", 0.7,
                f"document name '{doc_name}' does not match member record '{member_name}'"))

    if clinical:
        admission = parse_date(clinical.get("admission_date"))
        rx = parse_date(clinical.get("prescription_date"))
        reference = parse_date(clinical.get("discharge_date")) or parse_date(
            (claim or {}).get("service_date"))

        if admission and clinical.get("prior_discharge_date"):
            prior_end = parse_date(clinical["prior_discharge_date"])
            if prior_end and 0 < (admission - prior_end).days <= READMIT_DAYS:
                anomalies.append(_anomaly("readmission_30d", 0.7,
                    f"earlier stay ended {(admission - prior_end).days} days before this admission"))

        if admission:
            for prior in clinical_history:
                discharged = parse_date(prior.get("discharge_date"))
                if discharged and 0 < (admission - discharged).days <= READMIT_DAYS:
                    anomalies.append(_anomaly("readmission_30d", 0.7,
                        f"admitted {(admission - discharged).days} days after discharge on {discharged}"))
                    break

        if rx and reference and abs((rx - reference).days) >= RX_GAP_DAYS:
            anomalies.append(_anomaly("date_conflict", 0.6,
                f"prescription {rx} is {abs((rx - reference).days)} days from the service/discharge date {reference}"))

    return anomalies


def risk_level(score: float) -> str:
    if score >= 0.6:
        return "HIGH"
    if score >= 0.3:
        return "MEDIUM"
    return "LOW"


@node("fraud", writes="fraud")
def fraud_agent(state: CaseState) -> dict:
    prep = state.get("preprocessor") or {}
    fields = prep.get("fields") or {}
    claims = state.get("claims") or {}
    kind = claims.get("kind")
    extracted = claims.get("extracted_fields") or {}
    claim = extracted if kind == "CLAIM" else None
    clinical = extracted if kind == "CLINICAL" else None

    repo = get_repository()
    member = resolve_member(fields, repo)
    if member is None:
        return {"fraud_score": 0.0, "anomalies": [], "risk_level": "UNKNOWN", "member_id": None,
                "note": "member not identified; no claim history checked"}

    member_id = member["member_id"]
    anomalies = detect(
        claim=claim, clinical=clinical, member_name=member.get("full_name"),
        claim_history=repo.claims_for_member(member_id, state.get("case_id")),
        clinical_history=repo.clinical_events_for_member(member_id, state.get("case_id")),
    )
    survive = 1.0
    for a in anomalies:
        survive *= 1.0 - a["severity"]
    score = round(1.0 - survive, 3)
    return {"fraud_score": score, "anomalies": anomalies, "risk_level": risk_level(score),
            "member_id": member_id}
