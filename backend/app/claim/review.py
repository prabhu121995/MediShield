"""
Human in the loop. Runs only for an ESCALATE decision.

interrupt() pauses the run here and saves the whole state in the checkpoint. The claim
waits in the review queue until ops staff resume the run with
Command(resume={"decision", "note", "reviewer"}); this node then records their decision.
The agents' own decision is kept alongside it, never overwritten.

LangGraph re-runs this node from the top when it resumes, so nothing before interrupt()
has side effects.
"""

from __future__ import annotations

import time

from langgraph.types import interrupt

from app import audit
from app.claim._node import emit
from app.claim.state import ClaimState

DECISIONS = {"APPROVE", "REJECT", "ESCALATE"}


def human_review_agent(state: ClaimState) -> dict:
    out = state.get("decision") or {}
    answer = interrupt({
        "claim_id": state.get("claim_id"),
        "decision": out.get("decision"),
        "justification": out.get("justification"),
        "reasons": out.get("reasons", []),
        "fraud_tags": (state.get("fraud") or {}).get("tags", []),
    })
    started, t0 = audit.now(), time.perf_counter()
    decision = str((answer or {}).get("decision") or "").upper()
    if decision not in DECISIONS:
        raise ValueError(f"review needs a decision in {sorted(DECISIONS)}")
    review = {"decision": decision, "note": (answer or {}).get("note") or "",
              "reviewer": (answer or {}).get("reviewer") or "unknown",
              "reviewed_at": audit.now(), "original_decision": out.get("decision")}
    event = emit(state, "human_review", started, t0, {"decision": decision, "justification": review["note"]})
    return {"review": review, "trace": [event], "status": "REVIEWED"}
