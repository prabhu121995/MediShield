"""
Policy assistant: a clinical claims reviewer for the claims team.

For each procedure (CPT) code in a question, the assistant works in three steps, the way
a physician advisor would:

1. Clinical fit: does the diagnosis (ICD-10) in the question support the procedure? This
   step may use general clinical knowledge, and is labelled as such.
2. Policy: is the procedure covered under the policy? This step uses ONLY the retrieved
   policy clauses.
3. Verdict and next step: what the reviewer should check before deciding.

Each verdict is cross-checked against the clauses actually retrieved. If no retrieved
clause mentions the code, the answer says so, so a reader can tell an explicit clause
apart from the "covered unless excluded" default.

This is decision support for trained reviewers. It is not medical advice to a patient.
"""

from __future__ import annotations

import re
from typing import Any

from app import config
from app.llm import chat_json

CPT_RE = re.compile(r"(?<![\w-])(\d{5})(?![\w-])")
ICD10_RE = re.compile(r"(?<![\w.])([A-TV-Z][0-9][0-9AB](?:\.[0-9A-Z]{1,4})?)(?!\w)", re.IGNORECASE)
VERDICTS = ("covered", "excluded", "not_in_policy")
CLINICAL_FIT = ("supported", "doubtful", "not_assessed")
MAX_HISTORY_TURNS = config.POLICY_CHAT_MAX_HISTORY_TURNS      # .env
MAX_CLAUSE_CHARS = config.POLICY_CHAT_MAX_CLAUSE_CHARS
MAX_SOURCES = config.POLICY_CHAT_MAX_SOURCES

SYSTEM_PROMPT = """You are a senior clinical claims reviewer (physician-advisor style) for a health insurer.
For each CPT procedure code asked about, reason in three steps:

1. clinical_fit: does the ICD-10 diagnosis in the question support this procedure (medical
   necessity)? You may use general clinical knowledge here. Answer "supported", "doubtful"
   (the diagnosis does not usually lead to this procedure, or the pairing looks wrong), or
   "not_assessed" (no diagnosis was given). Explain briefly in clinical_note.
2. verdict: is the procedure covered under the policy? Use ONLY the policy clauses provided,
   never outside knowledge. Covered unless an exclusion applies. A code not mentioned is not
   excluded. CPT ranges such as 17000-17999 cover every code inside the range.
   Verdict must be one of "covered", "excluded", "not_in_policy" (clauses say nothing relevant).
3. next_step: one short sentence telling the reviewer what to check before deciding
   (for example "confirm the lesion count" or "obtain prior authorisation").

Return ONLY a JSON object with these keys:
- answer: a plain-English answer under 120 words. Name the plan when the clauses make it clear.
  Say so when the clauses do not cover the question.
- codes: one object per CPT code asked about, with keys:
    code (string), verdict, reason (one sentence quoting or paraphrasing the decisive clause),
    clinical_fit, clinical_note, next_step."""


def extract_cpt_codes(text: str) -> list[str]:
    seen: list[str] = []
    for code in CPT_RE.findall(text or ""):
        if code not in seen:
            seen.append(code)
    return seen


def extract_icd10_codes(text: str) -> list[str]:
    seen: list[str] = []
    for code in ICD10_RE.findall(text or ""):
        code = code.upper()
        if code not in seen:
            seen.append(code)
    return seen


def _unique_hits(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out = []
    for h in hits:
        if h["text"] not in seen:
            seen.add(h["text"])
            out.append(h)
    return out


def _history_block(history: list[dict[str, str]]) -> str:
    if not history:
        return "(new conversation)"
    return "\n".join(f"{t['role'].capitalize()}: {t['content'][:500]}" for t in history[-MAX_HISTORY_TURNS:])


def ask(question: str, history: list[dict[str, str]], store) -> dict[str, Any]:
    if store.count() == 0:
        return {"answer": "No policy document is indexed yet. Upload a policy PDF first.",
                "codes": [], "diagnoses": [], "sources": []}

    codes = extract_cpt_codes(question)
    diagnoses = extract_icd10_codes(question)
    hits = store.search(question, k=5)
    for code in codes:
        # Same wording as the coverage agent, so exclusion-table ranges come back.
        hits += store.search(f"CPT {code} excluded CPT code ranges and cosmetic exclusions", k=3)
    hits = _unique_hits(hits)

    clauses = "\n---\n".join(f"[S{i + 1}] ({h['source']}) {h['text'][:MAX_CLAUSE_CHARS]}"
                            for i, h in enumerate(hits))
    user = (f"Conversation so far:\n{_history_block(history)}\n\n"
            f"CPT procedure codes: {', '.join(codes) if codes else 'none'}\n"
            f"ICD-10 diagnosis codes: {', '.join(diagnoses) if diagnoses else 'none given'}\n\n"
            f"Question: {question}\n\nPolicy clauses:\n{clauses}")
    raw = chat_json(SYSTEM_PROMPT, user)

    model_codes = {str(c.get("code")): c for c in raw.get("codes") or [] if isinstance(c, dict)}
    results = []
    for code in codes:
        entry = model_codes.get(code, {})
        verdict = entry.get("verdict") if entry.get("verdict") in VERDICTS else "not_in_policy"
        fit = entry.get("clinical_fit") if entry.get("clinical_fit") in CLINICAL_FIT else "not_assessed"
        matched = [h for h in hits if code in h["text"]]
        results.append({
            "code": code,
            "verdict": verdict,
            "reason": str(entry.get("reason") or ""),
            "clinical_fit": fit,
            "clinical_note": str(entry.get("clinical_note") or ""),
            "next_step": str(entry.get("next_step") or ""),
            "cross_check": "matched" if matched else "no_clause_mentions_code",
            "source": matched[0]["source"] if matched else None,
            "clause": matched[0]["text"][:MAX_CLAUSE_CHARS] if matched else None,
        })

    sources = [{"source": h["source"], "excerpt": h["text"][:280]} for h in hits[:MAX_SOURCES]]
    return {"answer": str(raw.get("answer") or "I could not form an answer from the policy."),
            "codes": results, "diagnoses": diagnoses, "sources": sources}
