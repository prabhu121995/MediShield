"""Date parsing and member resolution shared by kyc and fraud."""

from __future__ import annotations

import re
from datetime import date, datetime


def parse_date(value) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y", "%Y/%m/%d",
                "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def has_identifier(fields: dict) -> bool:
    return bool(fields.get("member_id") or fields.get("policy_number")
                or (fields.get("patient_name") and fields.get("date_of_birth")))


POLICY_RE = re.compile(r"^MED-[A-Z]{3}-\d{5,}$")


def resolve_member(fields: dict, repo) -> dict | None:
    """Member id, then policy number, then name + DOB. Claim forms usually carry only the policy.
    Vision reads sometimes put a policy number in the member_id field, so a policy-shaped
    value is tried as a policy number too."""
    member_id = fields.get("member_id")
    if member_id and not POLICY_RE.match(member_id):
        member = repo.find_member(member_id)
        if member:
            return member
    policy = fields.get("policy_number") or (member_id if member_id and POLICY_RE.match(member_id) else None)
    if policy:
        member = repo.find_member_by_policy(policy)
        if member:
            return member
    dob = parse_date(fields.get("date_of_birth"))
    if fields.get("patient_name") and dob:
        return repo.find_member_by_name_dob(fields["patient_name"], dob)
    return None


def name_tokens(name: str | None) -> frozenset[str]:
    """A name as a set of lower-case words, middle initials dropped: 'Jane Q. Doe' == 'JANE DOE'."""
    words = re.sub(r"[^a-z ]", " ", (name or "").lower()).split()
    return frozenset(w for w in words if len(w) > 1)
