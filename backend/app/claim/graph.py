"""
LangGraph wiring for one claim (all documents uploaded together).

    START
      -> classify                               pass 1, one page per step; loops until every page
                                                is classified (type, image quality, blur)
      -> ingest_hold                            when a page is UNKNOWN, blurry or failed: interrupt(),
                                                the claim is ON_HOLD before any page is read, until
                                                a person continues, stops or re-uploads
      -> extract                                pass 2, preprocess + extract in one step per page
                                                (text, then fields); loops until every page is read
      -> ingest_hold                            only if a page could not be read and no person has
                                                looked at this run yet
      -> plan                                   (orchestrator: which agents apply)
      -> duplicate_check                        checkpoint, no model call: the patient already has a stored
                                                claim with the same DOS and amount -> decide directly
                                                (agents skipped), which escalates it to human_review
           -> kyc ................................           -> claims -> policy (claim form only) ---> fraud (deferred) -> decide (deferred)
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
    kyc_agent, plan_agent, policy_agent,
)
from app.claim.duplicate import duplicate_check
from app.claim.hold import classify_problems, ingest_hold_agent, read_problems
from app.claim.state import ClaimState


def _failed(state: ClaimState, key: str) -> bool:
    out = state.get(key)
    return isinstance(out, dict) and "error" in out


def route_after_classify(state: ClaimState) -> str:
    if len(state.get("classified") or []) < len(state.get("documents") or []):
        return "classify"
    return "ingest_hold" if classify_problems(state.get("classified") or []) else "extract"


def route_after_extract(state: ClaimState) -> str:
    if len(state.get("docs") or []) < len(state.get("documents") or []):
        return "extract"
    if read_problems(state.get("docs") or []) and not state.get("hold"):
        return "ingest_hold"
    return "plan"


def route_after_hold(state: ClaimState) -> str:
    if (state.get("hold") or {}).get("action") != "continue":
        return END
    return "plan" if len(state.get("docs") or []) >= len(state.get("documents") or []) else "extract"


def _planned_agents(state: ClaimState) -> list[str]:
    return [a for a in (state.get("plan") or {}).get("agents", []) if a in ("kyc", "claims")]


def route_after_plan(state: ClaimState) -> str:
    return "duplicate_check" if _planned_agents(state) else "decide"


def route_after_duplicate(state: ClaimState):
    if (state.get("duplicate") or {}).get("is_duplicate"):
        return "decide"
    return _planned_agents(state) or "decide"


def route_after_claims(state: ClaimState) -> str:
    if _failed(state, "claims"):
        return "fraud"
    return "policy" if (state.get("claims") or {}).get("claim") else "fraud"


def route_after_decide(state: ClaimState) -> str:
    return "human_review" if (state.get("decision") or {}).get("decision") == "ESCALATE" else END


def build_claim_graph(checkpointer=None):
    g = StateGraph(ClaimState)
    g.add_node("classify", classify_agent)
    g.add_node("extract", extract_agent)
    g.add_node("ingest_hold", ingest_hold_agent)
    g.add_node("plan", plan_agent)
    g.add_node("duplicate_check", duplicate_check)
    g.add_node("kyc", kyc_agent)
    g.add_node("claims", claims_agent)
    g.add_node("policy", policy_agent)
    g.add_node("fraud", fraud_agent, defer=True)
    g.add_node("decide", decide_agent, defer=True)
    g.add_node("human_review", human_review_agent)

    g.add_edge(START, "classify")
    g.add_conditional_edges("classify", route_after_classify, ["classify", "ingest_hold", "extract"])
    g.add_conditional_edges("extract", route_after_extract, ["extract", "ingest_hold", "plan"])
    g.add_conditional_edges("ingest_hold", route_after_hold, ["extract", "plan", END])
    g.add_conditional_edges("plan", route_after_plan, ["duplicate_check", "decide"])
    g.add_conditional_edges("duplicate_check", route_after_duplicate, ["kyc", "claims", "decide"])
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
