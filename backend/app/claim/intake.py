"""
Upload checks that read nothing but the file names, so they cost no model calls.

    name_types       the document types a file name points to (rules: filename_hints), so a
                     "claim_PT_19116.png" attached as an ID document can be refused
    patient_refs     the patient references in the file names (rules: patient_ref_pattern,
                     e.g. PT_19116), which are insurance member IDs
    resolve_patient  what a reference means: a registered patient, a member with no patient
                     yet (one is created at upload), or nobody known
    identify_member  after the pages are read: the member the claim is for, from what every
                     page prints (member ID / policy number, then name + date of birth)
"""

from __future__ import annotations

import re
import uuid
from collections import Counter

from app import audit, config, db

_DEFAULT_REF = r"PT_\d+"


def _words(file_name: str | None) -> set[str]:
    return set(re.split(r"[^a-z0-9]+", (file_name or "").lower())) - {""}


def name_types(file_name: str | None, rules: dict | None = None) -> set[str]:
    """Every document type with a filename_hints word in the name; empty for a name like 'aaaaaa.png'."""
    words = _words(file_name)
    hints = (rules or config.load_rules()).get("filename_hints") or {}
    return {doc_type for doc_type, words_for in hints.items() if words & set(words_for)}


def patient_refs(file_names: list[str | None], rules: dict | None = None) -> set[str]:
    pattern = re.compile((rules or config.load_rules()).get("patient_ref_pattern") or _DEFAULT_REF, re.IGNORECASE)
    return {m.upper() for name in file_names for m in pattern.findall(name or "")}


def resolve_patient(member_id: str) -> dict:
    """{"member_id", "status": registered | new | unknown, "patient", "member"}."""
    found = db.find_patient_by_member(member_id)
    if found is not None:
        return {"member_id": member_id, "status": "registered", "patient": found, "member": None}
    member = db.get_repository().find_member(member_id)
    if member is not None:
        return {"member_id": member_id, "status": "new", "patient": None,
                "member": {"full_name": member["full_name"], "policy_number": member.get("policy_number")}}
    return {"member_id": member_id, "status": "unknown", "patient": None, "member": None}


def identify_member(docs: list[dict], kyc_member: dict | None = None) -> tuple[dict | None, str | None]:
    """(member, how it was found) for a claim's read pages, or (None, None).

    Pages print the policy number as often as the member ID, and either one in either field,
    so every member_id / policy_number value on every page is tried against both member
    columns, the value seen on most pages first. Then name + date of birth (one exact member),
    then the member KYC already picked."""
    repo = db.get_repository()
    values: Counter[str] = Counter()
    where: dict[str, str] = {}
    for d in docs:
        f = d.get("fields") or {}
        for key in ("member_id", "policy_number"):
            v = str(f.get(key) or "").strip().upper()
            if v:
                values[v] += 1
                where.setdefault(v, f"{key} {v} on {d.get('file_name') or d.get('doc_type')}")
    for v, _ in values.most_common():
        member = repo.find_member(v) or repo.find_member_by_policy(v)
        if member is not None:
            return member, where[v]
    for d in docs:
        f = d.get("fields") or {}
        if f.get("patient_name") and f.get("date_of_birth"):
            member = repo.find_member_by_name_dob(f["patient_name"], f["date_of_birth"])
            if member is not None:
                return member, f"name and date of birth on {d.get('file_name') or d.get('doc_type')}"
    if kyc_member and kyc_member.get("member_id"):
        return kyc_member, "KYC name match"
    return None, None


def register_patient(member_id: str, created_by: str) -> str | None:
    """The patient for this member ID, created from the member record if there is none yet.
    None when no member has that ID."""
    found = db.find_patient_by_member(member_id)
    if found is not None:
        return found["patient_id"]
    member = db.get_repository().find_member(member_id)
    if member is None:
        return None
    data = {c: None for c in db.PATIENT_COLUMNS}
    data.update(full_name=member["full_name"], date_of_birth=member.get("date_of_birth"),
                policy_number=member.get("policy_number"), member_id=member_id)
    patient_id = str(uuid.uuid4())
    db.insert_patient(patient_id, data, created_by=created_by)
    audit.record_entity("patient", patient_id, created_by, "patient_created",
                        detail={"full_name": data["full_name"], "member_id": member_id, "source": "claim upload"})
    return patient_id
