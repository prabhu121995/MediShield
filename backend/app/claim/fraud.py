"""
Fraud agent: the six signal tags of dataset_summary.md section 3, checked across every
document in the claim and against the member's history in the database.

Tag                 Rule (parameters and severities: pipeline_rules.json "fraud")
id_expired          an ID page's expiry date is before the treatment date (insurance cards
                    are left to KYC, which rejects an expired card)
dob_mismatch        the date of birth differs between pages, or from the member record
policy_mismatch     the policy number differs between pages, or from the member record
duplicate_claim     two claim forms in this claim with the same claim number, or the same
                    service date and amount; or a stored claim with the same date and amount
amount_under_10k    billed total in [low, high): just under the review limit
proc_diag_mismatch  a CPT in procedure_ranges with no diagnosis starting with
                    required_diagnosis_prefixes (e.g. maternity CPT without an O-chapter ICD)
name_mismatch       the patient name differs between documents, or from the member record
                    (middle initials ignored)
readmission_30d     two different stays (claim form, discharge summary, history) where the
                    later admission is within `days` of the earlier discharge, or a prior
                    discharge the summary itself reports
date_conflict       prescription date `days` or more from the service/discharge date
missing_field       a medical page lacks a required field (pipeline_rules document_types
                    `required`), or the page itself marks a field missing or illegible
                    (e.g. an unsigned claim form or prescription)

score = 1 - product(1 - severity). Independent signals compound; one HIGH signal is
enough to reach the 0.3 escalation line on its own.
"""

from __future__ import annotations

from datetime import date

from app import config
from app.agents._util import name_tokens, parse_date
from app.claim._node import claim_node
from app.claim.state import ClaimState
from app.db import get_repository


def rule(tag: str) -> dict:
    return config.RULES["fraud"][tag]


def _flag(tag: str, detail: str, evidence: list[str]) -> dict:
    return {"type": tag, "severity": rule(tag)["severity"], "detail": detail, "evidence": evidence}


def duplicates(claim: dict | None, others: list[dict], history: list[dict]) -> list[dict]:
    if not claim:
        return []
    f = claim["extracted_fields"]
    for o in others:
        g = o["extracted_fields"]
        same_no = f.get("claim_number") and f.get("claim_number") == g.get("claim_number")
        same_bill = f.get("service_date") and f.get("service_date") == g.get("service_date") \
            and f.get("claim_amount") == g.get("claim_amount")
        if same_no or same_bill:
            why = f"claim number {f.get('claim_number')}" if same_no else "service date and amount"
            return [_flag("duplicate_claim", f"{o['file_name']} repeats {claim['file_name']} ({why})",
                          [claim["file_name"], o["file_name"]])]
    service = parse_date(f.get("service_date"))
    for prior in history:
        if service and parse_date(prior.get("service_date")) == service \
                and prior.get("claim_amount") == f.get("claim_amount"):
            return [_flag("duplicate_claim", f"same service date and amount as stored claim {prior['case_id']}",
                          [claim["file_name"]])]
    return []


def structuring(claim: dict | None) -> list[dict]:
    amount = (claim or {}).get("extracted_fields", {}).get("claim_amount")
    r = rule("amount_under_10k")
    if amount is not None and r["low"] <= amount < r["high"]:
        return [_flag("amount_under_10k", f"billed ${amount:,.2f}, just under the ${r['high']:,.0f} review threshold",
                      [claim["file_name"]])]
    return []


def proc_diag(claim: dict | None, clinical: list[dict]) -> list[dict]:
    if not claim:
        return []
    f = claim["extracted_fields"]
    r = rule("proc_diag_mismatch")
    flagged = [c["code"] for c in f.get("cpt_codes") or [] if c["code"].isdigit()
               and any(lo <= int(c["code"]) <= hi for lo, hi in r["procedure_ranges"])]
    icd = list(f.get("icd10_codes") or []) + [i for c in clinical for i in c["extracted_fields"].get("icd10_codes") or []]
    if flagged and not any(code.upper().startswith(tuple(r["required_diagnosis_prefixes"])) for code in icd):
        return [_flag("proc_diag_mismatch",
                      f"CPT {', '.join(flagged)} billed without a matching diagnosis; diagnoses "
                      f"{', '.join(f.get('icd10_codes') or []) or 'none'}", [claim["file_name"]])]
    return []


def names(docs: list[dict], member: dict | None) -> list[dict]:
    seen = [(d["file_name"], d["fields"]["patient_name"]) for d in docs
            if (d.get("fields") or {}).get("patient_name")]
    if member and member.get("full_name"):
        seen.append(("member record", member["full_name"]))
    distinct = {name_tokens(n) for _, n in seen if name_tokens(n)}
    if len(distinct) > 1:
        listing = "; ".join(f"{src}: '{n}'" for src, n in seen)
        return [_flag("name_mismatch", f"patient name differs across documents ({listing})",
                      [src for src, _ in seen])]
    return []


def stays(claim: dict | None, clinical: list[dict]) -> list[tuple[date, date | None, str]]:
    out = []
    if claim:
        f = claim["extracted_fields"]
        if parse_date(f.get("admission_date")):          # an inpatient claim carries its stay
            out.append((parse_date(f["admission_date"]), parse_date(f.get("discharge_date")), claim["file_name"]))
    for c in clinical:
        f = c["extracted_fields"]
        if c["kind"] == "DISCHARGE_SUMMARY" and parse_date(f.get("admission_date")):
            out.append((parse_date(f["admission_date"]), parse_date(f.get("discharge_date")), c["file_name"]))
    return out


def readmission(claim: dict | None, clinical: list[dict], history: list[dict]) -> list[dict]:
    for c in clinical:
        f = c["extracted_fields"]
        admitted, prior = parse_date(f.get("admission_date")), parse_date(f.get("prior_discharge_date"))
        if admitted and prior and 0 < (admitted - prior).days <= rule("readmission_30d")["days"]:
            return [_flag("readmission_30d", f"{c['file_name']} reports a prior stay ending {prior}, "
                          f"{(admitted - prior).days} days before this admission", [c["file_name"]])]

    episodes = stays(claim, clinical)
    episodes += [(parse_date(h["admission_date"]), parse_date(h.get("discharge_date")), f"stored claim {h['case_id']}")
                 for h in history if parse_date(h.get("admission_date"))]
    by_admission: dict[date, tuple] = {}
    for e in episodes:                                       # same admission date = same stay
        by_admission.setdefault(e[0], e)
    ordered = sorted(by_admission.values(), key=lambda e: e[0])
    for earlier, later in zip(ordered, ordered[1:]):
        end = earlier[1] or earlier[0]
        gap = (later[0] - end).days
        if gap <= rule("readmission_30d")["days"]:
            how = "overlaps the earlier stay" if gap < 0 else f"{gap} days after discharge"
            return [_flag("readmission_30d",
                          f"admission {later[0]} ({later[2]}) {how} {earlier[0]}–{end} ({earlier[2]})",
                          [earlier[2], later[2]])]
    return []


def rx_dates(claim: dict | None, clinical: list[dict]) -> list[dict]:
    refs = []
    if claim and parse_date(claim["extracted_fields"].get("service_date")):
        refs.append((parse_date(claim["extracted_fields"]["service_date"]), f"service date ({claim['file_name']})"))
    for c in clinical:
        if c["kind"] == "DISCHARGE_SUMMARY" and parse_date(c["extracted_fields"].get("discharge_date")):
            refs.append((parse_date(c["extracted_fields"]["discharge_date"]), f"discharge date ({c['file_name']})"))
    for c in clinical:
        rx = parse_date(c["extracted_fields"].get("prescription_date")) if c["kind"] == "PRESCRIPTION" else None
        if not rx or not refs:
            continue
        ref, label = min(refs, key=lambda r: abs((rx - r[0]).days))       # the closest reference
        if abs((rx - ref).days) >= rule("date_conflict")["days"]:
            return [_flag("date_conflict", f"prescription dated {rx} is {abs((rx - ref).days)} days from the "
                          f"{label} {ref}", [c["file_name"]])]
    return []


def treatment_date(docs: list[dict]) -> tuple[date | None, str]:
    for wanted, key in config.RULES["treatment_date_sources"]:
        for d in docs:
            value = parse_date((d.get("fields") or {}).get(key)) if d.get("doc_type") == wanted else None
            if value:
                return value, f"{key.replace('_', ' ')} on {d.get('file_name')}"
    return None, ""


def id_expiry(docs: list[dict], skip: set[str] = frozenset()) -> list[dict]:
    ref, source = treatment_date(docs)
    for d in docs:
        if d.get("file_name") in skip:
            continue
        expiry = parse_date((d.get("fields") or {}).get("id_expiry_date")) if d.get("doc_type") == "ID_DOCUMENT" else None
        if ref and expiry and expiry < ref:
            return [_flag("id_expired", f"{d['file_name']} expired {expiry}, before the {source} ({ref})", [d["file_name"]])]
    return []


def _differences(docs: list[dict], field: str, member_value, parse=lambda v: v) -> dict:
    seen: dict = {}
    for d in docs:
        value = parse((d.get("fields") or {}).get(field))
        if value:
            seen.setdefault(value, []).append(d["file_name"])
    member_value = parse(member_value) if member_value else None
    if member_value:
        seen.setdefault(member_value, []).append("member record")
    return seen if len(seen) > 1 else {}


def dob_mismatch(docs: list[dict], member: dict | None) -> list[dict]:
    seen = _differences(docs, "date_of_birth", (member or {}).get("date_of_birth"), parse_date)
    if seen:
        listing = "; ".join(f"{v}: {', '.join(src)}" for v, src in seen.items())
        return [_flag("dob_mismatch", f"date of birth differs ({listing})", [s for src in seen.values() for s in src])]
    return []


def policy_mismatch(docs: list[dict], member: dict | None) -> list[dict]:
    seen = _differences(docs, "policy_number", (member or {}).get("policy_number"))
    if seen:
        listing = "; ".join(f"{v}: {', '.join(src)}" for v, src in seen.items())
        return [_flag("policy_mismatch", f"policy number differs ({listing})", [s for src in seen.values() for s in src])]
    return []


def missing_fields(docs: list[dict]) -> list[dict]:
    from app.claim.claims import schema_errors      # the claims agent only extracts; the flag is ours
    rules = config.RULES
    medical = set(rules["medical_documents"])
    out = []
    for d in docs:
        if d.get("doc_type") not in medical:
            continue
        errors = schema_errors(d, rules)
        if errors:
            listed = ", ".join(e["field"] + (" (marked missing on the page)" if e["code"] == "MARKED_MISSING" else "")
                               for e in errors)
            out.append(_flag("missing_field", f"{d['file_name']} lacks {listed}", [d["file_name"]]))
    return out


def score(anomalies: list[dict]) -> float:
    survive = 1.0
    for a in anomalies:
        survive *= 1.0 - a["severity"]
    return round(1.0 - survive, 3)


def risk_level(value: float) -> str:
    return "HIGH" if value >= 0.6 else "MEDIUM" if value >= 0.3 else "LOW"


def assess(state: ClaimState, repo) -> dict:
    docs = state.get("docs") or []
    claims = state.get("claims") or {}
    claims = {} if "error" in claims else claims
    claim, clinical = claims.get("claim"), claims.get("clinical") or []
    kyc = state.get("kyc") or {}
    member = None if "error" in kyc else kyc.get("member")

    exclude = state.get("claim_id")
    claim_history = repo.claims_for_member(member["member_id"], exclude) if member else []
    stay_history = repo.clinical_events_for_member(member["member_id"], exclude) if member else []

    anomalies = (id_expiry(docs, set(kyc.get("insurance_cards") or [])) + dob_mismatch(docs, member) + policy_mismatch(docs, member) + duplicates(claim, claims.get("additional_claims") or [], claim_history)
                 + structuring(claim) + proc_diag(claim, clinical) + names(docs, member)
                 + readmission(claim, clinical, stay_history) + rx_dates(claim, clinical) + missing_fields(docs))
    value = score(anomalies)
    tags = [a["type"] for a in anomalies]
    return {
        "fraud_score": value,
        "risk_level": risk_level(value),
        "tags": tags,
        "anomalies": anomalies,
        "member_id": member["member_id"] if member else None,
        "checks_run": list(config.RULES["fraud"]),
        "history_checked": {"claims": len(claim_history), "stays": len(stay_history)},
        "confidence": 0.9 if (claim or clinical) else 0.7,
        "summary": f"score {value:.2f} ({risk_level(value)})" + (f": {', '.join(tags)}" if tags else ": no signals"),
    }


@claim_node("fraud", writes="fraud")
def fraud_agent(state: ClaimState) -> dict:
    return assess(state, get_repository())
