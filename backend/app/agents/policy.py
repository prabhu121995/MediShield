"""
Stage 4 -- is each procedure code covered?

For every CPT code on the claim, retrieve the closest policy clauses from the
Qdrant store. Then ask the LLM for a coverage decision that is grounded only in
those clauses. The result is validated against PolicyDecision, so the return
type is fixed even when the model's JSON is not.

If no policy has been ingested, the agent raises. That is ESCALATE, because
we cannot judge coverage without the policy.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app import config
from app.agents._base import node
from app.llm import chat_json
from app.policy_store import get_policy_store
from app.state import CaseState

MAX_CLAUSE_CHARS = config.POLICY_MAX_CLAUSE_CHARS     # .env; a shorter cut can drop table rows

SYSTEM_PROMPT = """You decide whether medical procedures are covered by an insurance policy.
Use ONLY the policy clauses provided. Do not use outside knowledge.
A procedure is covered unless an exclusion in the clauses applies to it. A code that is
simply not mentioned is not excluded: it is covered, subject to the other terms.
CPT codes in exclusions may be written as ranges (for example 17000-17999). A code that
falls inside an excluded range is excluded.
Return ONLY a JSON object with these keys:
- covered: true if every listed procedure is covered, false if any is excluded or not covered
- coverage_percentage: number from 0 to 100 (100 if covered, 0 if excluded)
- policy_clause: the clause text that decided the answer, quoted or closely paraphrased
- exclusions: list of strings, one per excluded procedure, naming the code and reason
If the clauses do not say, set covered to false and explain in policy_clause."""


class PolicyDecision(BaseModel):
    covered: bool
    coverage_percentage: float = Field(ge=0.0, le=100.0)
    policy_clause: str
    exclusions: list[str] = Field(default_factory=list)
    cpt_codes_checked: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("exclusions", mode="before")
    @classmethod
    def _as_list(cls, v):
        return [] if v in (None, "") else ([v] if isinstance(v, str) else v)


def decide_coverage(cpt_codes: list[str], store) -> PolicyDecision:
    if not cpt_codes:
        raise ValueError("no CPT codes to check coverage for")
    if store.count() == 0:
        raise RuntimeError("no policy document ingested yet; upload the policy PDF first")

    seen: set[str] = set()
    clauses, top_scores = [], []
    for code in cpt_codes:
        # Query with the exclusion table's own wording, so the range rows come back.
        hits = store.search(f"CPT {code} excluded CPT code ranges and cosmetic exclusions", k=3)
        top_scores.append(hits[0]["score"] if hits else 0.0)
        for h in hits:
            if h["text"] not in seen:              # one clause often matches several codes
                seen.add(h["text"])
                clauses.append(f"[{h['source']}] {h['text'][:MAX_CLAUSE_CHARS]}")

    user = ("Procedure codes on the claim: " + ", ".join(cpt_codes)
            + "\n\nPolicy clauses:\n" + "\n---\n".join(clauses))
    raw = chat_json(SYSTEM_PROMPT, user)
    # Retrieval confidence: the retriever found a clearly related clause for every code.
    confidence = 0.9 if min(top_scores) >= 0.3 else 0.5
    return PolicyDecision(**raw, cpt_codes_checked=cpt_codes, confidence=confidence)


@node("policy", writes="policy")
def policy_agent(state: CaseState) -> dict:
    claims = state.get("claims") or {}
    if claims.get("kind") != "CLAIM":
        raise ValueError("policy check needs a claim document")
    codes = [c["code"] for c in claims.get("extracted_fields", {}).get("cpt_codes") or []]
    return decide_coverage(codes, get_policy_store()).model_dump()
