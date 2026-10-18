"""
Policy agent: is every procedure on the claim form covered by the member's plan?

RAG: clauses are retrieved from the Qdrant policy store, one query per CPT code plus the
configured general query (rules: policy.retrieval_query). When the member's plan is known
(member record), only that plan's documents are used. The LLM then decides coverage from
those clauses only, and its answer is validated against PolicyDecision. Retrieval settings
live in pipeline_rules.json; the model comes from .env.
"""

from __future__ import annotations

from app import config
from app.agents.policy import MAX_CLAUSE_CHARS, SYSTEM_PROMPT, PolicyDecision
from app.claim._node import claim_node
from app.claim.state import ClaimState
from app.db import get_repository
from app.llm import chat_json
from app.policy_store import get_policy_store


def member_plan(policy_number: str | None, repo) -> str | None:
    member = repo.find_member_by_policy(policy_number) if policy_number else None
    return (member or {}).get("plan")


def decide(codes: list[str], plan: str | None, store, rules: dict | None = None) -> dict:
    rules = (rules or config.RULES)["policy"]
    if not codes:
        raise ValueError("the claim form lists no CPT codes to check")
    if store.count() == 0:
        raise RuntimeError("no policy document ingested yet; upload the policy PDF first")

    k = rules["clauses_per_query"]
    hits = store.search(rules["retrieval_query"], k=k)
    best = []
    for code in codes:
        found = store.search(f"CPT {code} {rules['retrieval_query']}", k=k)
        best.append(found[0]["score"] if found else 0.0)
        hits += found
    if plan:
        hits = [h for h in hits if plan.lower() in h["source"].lower()] or hits
    clauses = list(dict.fromkeys(f"[{h['source']}] {h['text'][:MAX_CLAUSE_CHARS]}" for h in hits))

    user = ("Procedure codes on the claim: " + ", ".join(codes)
            + (f"\nMember plan: {plan}" if plan else "") + "\n\nPolicy clauses:\n" + "\n---\n".join(clauses))
    raw = chat_json(SYSTEM_PROMPT, user)
    confidence = 0.9 if min(best) >= rules["min_retrieval_score"] else 0.5
    decision = PolicyDecision(**{k: raw.get(k) for k in ("covered", "coverage_percentage", "policy_clause", "exclusions")
                                 if raw.get(k) is not None},
                              cpt_codes_checked=codes, confidence=confidence)
    return {**decision.model_dump(), "plan": plan, "sources": sorted({h["source"] for h in hits}),
            "clauses_used": len(clauses), "notes": [],
            "summary": ("covered" if decision.covered else "not covered: " + "; ".join(decision.exclusions))}


@claim_node("policy", writes="policy")
def policy_agent(state: ClaimState) -> dict:
    claim = (state.get("claims") or {}).get("claim") or {}
    fields = claim.get("extracted_fields") or {}
    codes = [c["code"] for c in fields.get("cpt_codes") or []]
    return decide(codes, member_plan(fields.get("policy_number"), get_repository()), get_policy_store())
