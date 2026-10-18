"""
LangGraph wiring (WORKFLOW.md section 1).

    preprocessor -> classifier -> route
      CLAIM_FORM                      -> [kyc || claims] -> policy -> fraud -> orchestrator
      DISCHARGE_SUMMARY / PRESCRIPTION -> claims -> fraud -> orchestrator
      ID_DOCUMENT / POLICY_AMENDMENT   -> kyc -> orchestrator
      UNKNOWN or any upstream error    -> orchestrator (ESCALATE)

kyc and claims fan out in parallel for a claim form. The orchestrator is
deferred, so it runs once after every branch that feeds it has finished.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agents import (
    claims_agent,
    classifier_agent,
    fraud_agent,
    kyc_agent,
    orchestrator_agent,
    policy_agent,
    preprocessor_agent,
)
from app.state import CaseState


def _failed(state: CaseState, key: str) -> bool:
    out = state.get(key)
    return isinstance(out, dict) and "error" in out


def route_after_classifier(state: CaseState):
    if _failed(state, "classifier"):
        return "orchestrator"
    doc_type = (state.get("classifier") or {}).get("doc_type")
    if doc_type == "CLAIM_FORM":
        return ["kyc", "claims"]                       # parallel branches
    if doc_type in ("ID_DOCUMENT", "POLICY_AMENDMENT"):
        return "kyc"
    if doc_type in ("DISCHARGE_SUMMARY", "PRESCRIPTION"):
        return "claims"
    return "orchestrator"                              # UNKNOWN


def route_after_claims(state: CaseState) -> str:
    if _failed(state, "claims"):
        return "orchestrator"
    if (state.get("claims") or {}).get("kind") == "CLAIM":
        return "policy"
    return "fraud"


def route_after_policy(state: CaseState) -> str:
    return "orchestrator" if _failed(state, "policy") else "fraud"


def build_graph():
    g = StateGraph(CaseState)
    g.add_node("preprocessor", preprocessor_agent)
    g.add_node("classifier", classifier_agent)
    g.add_node("kyc", kyc_agent)
    g.add_node("claims", claims_agent)
    g.add_node("policy", policy_agent)
    g.add_node("fraud", fraud_agent)
    g.add_node("orchestrator", orchestrator_agent, defer=True)

    g.add_edge(START, "preprocessor")
    g.add_edge("preprocessor", "classifier")
    g.add_conditional_edges("classifier", route_after_classifier,
                            ["kyc", "claims", "orchestrator"])
    g.add_conditional_edges("claims", route_after_claims, ["policy", "fraud", "orchestrator"])
    g.add_conditional_edges("policy", route_after_policy, ["fraud", "orchestrator"])
    g.add_edge("kyc", "orchestrator")
    g.add_edge("fraud", "orchestrator")
    g.add_edge("orchestrator", END)
    return g.compile()


_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph
