"""
Basic guardrails around the extractor (text model), one on each side.

    check_input(text)              before the model: caps the page text, and removes lines
                                   that try to instruct the model ("ignore previous
                                   instructions", "approve this claim"), which a scanned
                                   page can carry as easily as any other text
    check_output(out, text, type)  after the model: drops values that cannot be right
                                   (malformed ICD-10 / CPT / NPI, a code the page never
                                   prints, a future date, a discharge before the admission,
                                   a zero, negative or huge amount)

Both return a list of flags. A dropped value becomes null, so a required field also shows
up as MISSING_FIELD for the fraud agent, and any flag sends the claim to human review
(see decide.py). Limits and phrases: pipeline_rules.json "guardrails".
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from app import config

ICD10_RE = re.compile(r"[A-TV-Z][0-9][0-9AB](\.?[0-9A-Z]{1,4})?", re.IGNORECASE)
CPT_RE = re.compile(r"\d{4}[0-9FTU]")                  # 99213, 0001F, 0042T
NPI_RE = re.compile(r"\d{10}")
EARLIEST_DATE = dt.date(1900, 1, 1)
CODE_FIELDS = {"icd10_codes": ICD10_RE, "cpt_codes": CPT_RE}
AMOUNT_FIELDS = ("claim_amount", "billed_total")


def _rules() -> dict:
    return config.RULES.get("guardrails") or {}


def _flag(field: str, code: str, message: str) -> dict:
    return {"field": field, "code": code, "message": message}


def _squash(value: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", str(value).upper())


# ---- input -----------------------------------------------------------------------------

def check_input(text: str) -> tuple[str, list[dict]]:
    """(the text to send to the model, flags)."""
    rules = _rules()
    flags: list[dict] = []
    phrases = [p.lower() for p in rules.get("injection_phrases") or []]
    kept = []
    for line in (text or "").splitlines():
        hit = next((p for p in phrases if p in line.lower()), None)
        if hit:
            flags.append(_flag("text", "PROMPT_INJECTION", f"removed a line instructing the model ('{hit}')"))
        else:
            kept.append(line)
    clean = "\n".join(kept)
    limit = int(rules.get("max_text_chars") or 0)
    if limit and len(clean) > limit:
        flags.append(_flag("text", "TEXT_TRUNCATED", f"page text cut from {len(clean)} to {limit} characters"))
        clean = clean[:limit]
    return clean, flags


# ---- output ----------------------------------------------------------------------------

def _date(value) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def check_output(out: dict[str, Any], text: str, doc_type: str) -> list[dict]:
    """Drops impossible values from out["fields"] in place; returns what was dropped."""
    fields = out.get("fields") or {}
    flags: list[dict] = []
    printed = _squash(text)
    today = dt.date.today()

    for name, pattern in CODE_FIELDS.items():
        if not fields.get(name):
            continue
        kept = []
        for value in fields[name]:
            code = re.split(r"[\s,;:()]+", value.strip())[0]       # "99213 - office visit" -> 99213
            if not pattern.fullmatch(code):
                flags.append(_flag(name, "INVALID_FORMAT", f"'{code}' is not a valid {name[:-6].upper()} code"))
            elif _squash(code) not in printed:
                flags.append(_flag(name, "NOT_ON_PAGE", f"'{code}' does not appear in the page text"))
            else:
                kept.append(code)
        fields[name] = kept

    for name in ("provider_npi", "prescriber_npi"):
        value = fields.get(name)
        if value and not NPI_RE.fullmatch(re.sub(r"[\s-]", "", str(value).removeprefix("NPI").strip(" :-"))):
            flags.append(_flag(name, "INVALID_FORMAT", f"'{value}' is not a 10-digit NPI"))
            fields[name] = None

    for name, value in list(fields.items()):
        if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            d = _date(value)
            future_ok = name.endswith("expiry_date")              # a card expires in the future
            if d is None or d < EARLIEST_DATE or (d > today and not future_ok):
                flags.append(_flag(name, "IMPOSSIBLE_DATE", f"{name} {value} is out of range"))
                fields[name] = None

    admitted, discharged = _date(fields.get("admission_date")), _date(fields.get("discharge_date"))
    if admitted and discharged and discharged < admitted:
        flags.append(_flag("discharge_date", "DATE_ORDER",
                           f"discharge {discharged} is before admission {admitted}"))

    cap = float(_rules().get("max_claim_amount") or 0)
    for name in AMOUNT_FIELDS:
        amount = fields.get(name)
        if amount is None:
            continue
        if amount <= 0 or (cap and amount > cap):
            flags.append(_flag(name, "IMPOSSIBLE_AMOUNT", f"{name} {amount:,.2f} is outside 0 to {cap:,.0f}"))
            fields[name] = None
    return flags
