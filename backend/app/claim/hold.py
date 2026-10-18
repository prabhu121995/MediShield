"""
Human in the loop in the document queue.

Every page is classified first. When a page is UNKNOWN (the classifier could not tell what it
is), blurry or unreadable, or its classification failed, the run stops here with interrupt()
before any page is read, and the claim is put ON_HOLD. The same hold is used once more if a
page then cannot be read (preprocess + extract failed) and no person has looked yet.
A person then chooses:

    continue   go on with the pages as they are (problem pages escalate at the decision)
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


def classify_problems(pages: list[dict]) -> list[dict]:
    """Pages that stop the claim after classification, before anything is read."""
    out = []
    for d in pages or []:
        if d.get("error"):
            reason = f"could not be classified: {d['error']}"
        elif d.get("doc_type") == "UNKNOWN":
            reason = (f"document type not recognised (model said {d.get('classified_as') or 'UNKNOWN'} "
                      f"at {float(d.get('classifier_confidence') or 0):.0%} confidence)")
        elif d.get("is_blurry"):
            reason = (f"page is {'unreadable' if d.get('image_quality') == 'unreadable' else 'blurry'} "
                      f"(model: {d.get('image_quality')}, blur variance {d.get('blur_variance')})")
        else:
            continue
        out.append({"doc_id": d.get("doc_id"), "file_name": d.get("file_name"), "reason": reason})
    return out


def read_problems(docs: list[dict]) -> list[dict]:
    """Pages whose preprocess + extract step failed."""
    return [{"doc_id": d.get("doc_id"), "file_name": d.get("file_name"), "reason": f"could not be read: {d['error']}"}
            for d in docs or [] if d.get("error") and d.get("failed_step") == "extract"]


def stage_of(state: ClaimState) -> str:
    """Where the queue is: "classify" until every page is classified and read, else "extract"."""
    return "extract" if len(state.get("docs") or []) >= len(state.get("documents") or []) > 0 else "classify"


def problem_pages(state: ClaimState) -> list[dict]:
    if stage_of(state) == "extract":
        return read_problems(state.get("docs") or [])
    return classify_problems(state.get("classified") or [])


def ingest_hold_agent(state: ClaimState) -> dict:
    stage = stage_of(state)
    pages = problem_pages(state)
    answer = interrupt({"stage": "ingest", "step": stage, "claim_id": state.get("claim_id"), "pages": pages})
    started_at, t0 = audit.now(), time.perf_counter()
    action = str((answer or {}).get("action") or "").lower()
    if action not in ACTIONS:
        raise ValueError(f"hold needs an action in {sorted(ACTIONS)}")
    hold = {"stage": "ingest", "step": stage, "action": action, "note": (answer or {}).get("note") or "",
            "reviewer": (answer or {}).get("reviewer") or "unknown", "decided_at": audit.now(), "pages": pages}
    event = emit(state, "ingest_hold", started_at, t0, {"action": action, "note": hold["note"], "step": stage})
    return {"hold": hold, "trace": [event], "status": f"HOLD_{action.upper()}"}
