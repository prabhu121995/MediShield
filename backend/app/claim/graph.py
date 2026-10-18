"""
LangGraph wiring for one claim (all documents uploaded together).

    START
      -> preprocess -> classify -> extract      (one page per pass; loops until every page is read)
      -> ingest_hold                            only when a page failed or is UNKNOWN: interrupt(),
                                                the claim is ON_HOLD until a person continues or stops
      -> plan                                   (orchestrator: which agents apply)
           -> kyc ................................\
           -> claims -> policy (claim form only) ---> fraud (deferred) -> decide (deferred)
      plan -> decide directly when no page could be routed
      decide -> human_review (ESCALATE only)    interrupt(): the run pauses at a checkpoint
                                                until a person resumes it with their decision
      -> END

kyc and claims run in parallel. fraud and decide are deferred nodes, so each runs once,
after every branch that feeds it has finished (LangGraph state aggregation).

Every run is checkpointed in MySQL (app.checkpoint_mysql.MySQLSaver), one thread per run,
so an escalated claim waits at human_review across server restarts and resumes exactly where
it stopped.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.claim.agents import (
    claims_agent, classify_agent, decide_agent, extract_agent, fraud_agent, human_review_agent,
    kyc_agent, plan_agent, policy_agent, preprocess_agent,
)
from app.claim.hold import ingest_hold_agent, problem_pages
from app.claim.state import ClaimState


def _failed(state: ClaimState, key: str) -> bool:
    out = state.get(key)
    return isinstance(out, dict) and "error" in out


def route_after_extract(state: ClaimState) -> str:
    if len(state.get("docs") or []) < len(state.get("documents") or []):
        return "preprocess"
    return "ingest_hold" if problem_pages(state.get("docs") or []) else "plan"


def route_after_hold(state: ClaimState) -> str:
    return "plan" if (state.get("hold") or {}).get("action") == "continue" else END


def route_after_plan(state: ClaimState):
    agents = [a for a in (state.get("plan") or {}).get("agents", []) if a in ("kyc", "claims")]
    return agents or "decide"


def route_after_claims(state: ClaimState) -> str:
    if _failed(state, "claims"):
        return "fraud"
    return "policy" if (state.get("claims") or {}).get("claim") else "fraud"


def route_after_decide(state: ClaimState) -> str:
    return "human_review" if (state.get("decision") or {}).get("decision") == "ESCALATE" else END


def build_claim_graph(checkpointer=None):
    g = StateGraph(ClaimState)
    g.add_node("preprocess", preprocess_agent)
    g.add_node("classify", classify_agent)
    g.add_node("extract", extract_agent)
    g.add_node("ingest_hold", ingest_hold_agent)
    g.add_node("plan", plan_agent)
    g.add_node("kyc", kyc_agent)
    g.add_node("claims", claims_agent)
    g.add_node("policy", policy_agent)
    g.add_node("fraud", fraud_agent, defer=True)
    g.add_node("decide", decide_agent, defer=True)
    g.add_node("human_review", human_review_agent)

    g.add_edge(START, "preprocess")
    g.add_edge("preprocess", "classify")
    g.add_edge("classify", "extract")
    g.add_conditional_edges("extract", route_after_extract, ["preprocess", "ingest_hold", "plan"])
    g.add_conditional_edges("ingest_hold", route_after_hold, ["plan", END])
    g.add_conditional_edges("plan", route_after_plan, ["kyc", "claims", "decide"])
    g.add_conditional_edges("claims", route_after_claims, ["policy", "fraud"])
    g.add_edge("kyc", "fraud")
    g.add_edge("policy", "fraud")
    g.add_edge("fraud", "decide")
    g.add_conditional_edges("decide", route_after_decide, ["human_review", END])
    g.add_edge("human_review", END)
    return g.compile(checkpointer=checkpointer)


_graph = None


def checkpointer():
    from app.checkpoint_mysql import MySQLSaver
    return MySQLSaver()


def get_claim_graph():
    global _graph
    if _graph is None:
        _graph = build_claim_graph(checkpointer())
    return _graph


def set_claim_graph(graph) -> None:
    global _graph
    _graph = graph
