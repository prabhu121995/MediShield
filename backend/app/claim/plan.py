"""
Orchestrator, first half: after every page is read, decide which agents apply.

    kyc     whenever any page was recognised (identity can be read from any form)
    claims  when a medical document is present (rules: medical_documents);
            claims hands the claim form on to policy

The plan also records what is missing (rules: required_documents). decide() escalates a
claim that lacks one.
"""

from __future__ import annotations

from collections import Counter

from app import config
from app.claim._node import claim_node
from app.claim.state import ClaimState


def make_plan(docs: list[dict], rules: dict | None = None) -> dict:
    rules = rules or config.RULES
    counts = Counter(d.get("doc_type", "UNKNOWN") for d in docs)
    agents = []
    if any(d.get("doc_type") not in (None, "UNKNOWN") for d in docs):
        agents.append("kyc")
    if any(d.get("doc_type") in rules["medical_documents"] for d in docs):
        agents.append("claims")
    return {
        "agents": agents,
        "document_counts": dict(counts),
        "unknown_documents": [d["file_name"] for d in docs if d.get("doc_type") in (None, "UNKNOWN")],
        "unreadable_documents": [d["file_name"] for d in docs if d.get("error")],
        "missing_required": [t for t in rules["required_documents"] if counts.get(t, 0) == 0],
    }


@claim_node("plan", writes="plan")
def plan_agent(state: ClaimState) -> dict:
    return make_plan(state.get("docs") or [])
