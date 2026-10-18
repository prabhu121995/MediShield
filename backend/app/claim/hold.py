"""
Human in the loop between page reading and the claim agents.

Step one (preprocess -> classify -> extract on every page) must be complete before the claim
agents run. When a page could not be read (a model call failed or timed out) or the
classifier could not tell what it is (UNKNOWN), the run pauses here with interrupt() and the
claim is put ON_HOLD. A person then chooses:

    continue   run the claim agents with the pages as they are (unread pages escalate)
    stop       end the run here; the claim is STOPPED
    re-upload  replace that page or every page (handled by the service, which starts a new run)

LangGraph re-runs this node from the top when it resumes, so nothing before interrupt()
has side effects.
"""

from __future__ import annotations

import time

from langgraph.types import interrupt

from app import audit
from app.claim._node import emit
from app.claim.state import ClaimState

ACTIONS = {"continue", "stop"}


def problem_pages(docs: list[dict]) -> list[dict]:
    """Pages a person has to look at before the claim agents run."""
    out = []
    for d in docs or []:
        if d.get("error"):
            reason = f"could not be read: {d['error']}"
        elif d.get("doc_type") == "UNKNOWN":
            reason = (f"document type not recognised (model said {d.get('classified_as') or 'UNKNOWN'} "
                      f"at {float(d.get('classifier_confidence') or 0):.0%} confidence)")
        else:
            continue
        out.append({"doc_id": d.get("doc_id"), "file_name": d.get("file_name"), "reason": reason})
    return out


def ingest_hold_agent(state: ClaimState) -> dict:
    pages = problem_pages(state.get("docs") or [])
    answer = interrupt({"stage": "ingest", "claim_id": state.get("claim_id"), "pages": pages})
    started_at, t0 = audit.now(), time.perf_counter()
    action = str((answer or {}).get("action") or "").lower()
    if action not in ACTIONS:
        raise ValueError(f"hold needs an action in {sorted(ACTIONS)}")
    hold = {"stage": "ingest", "action": action, "note": (answer or {}).get("note") or "",
            "reviewer": (answer or {}).get("reviewer") or "unknown", "decided_at": audit.now(), "pages": pages}
    event = emit(state, "ingest_hold", started_at, t0, {"action": action, "note": hold["note"]})
    return {"hold": hold, "trace": [event], "status": f"HOLD_{action.upper()}"}
