"""
Wrapper for every claim-graph agent: timing, a trace event, an audit row, error capture.
A failing agent writes {"error": ...} as its output instead of crashing the claim, and
the orchestrator turns that into ESCALATE.
"""

from __future__ import annotations

import functools
import time
from typing import Any, Callable

from app import audit
from app.claim.state import ClaimState, TraceEvent

AUDIT_KEYS = ("error", "decision", "confidence", "kyc_passed", "flags", "covered", "exclusions",
              "schema_valid", "fraud_score", "risk_level", "tags", "agents", "justification",
              "doc_type", "file_name", "read_confidence", "classifier_confidence", "image_quality", "is_blurry")


def summarize(output: Any) -> dict[str, Any]:
    """The small part of an agent output worth keeping in the audit row."""
    if not isinstance(output, dict):
        return {}
    return {k: output[k] for k in AUDIT_KEYS if k in output}


def emit(state: ClaimState, agent: str, started: str, t0: float, output: dict,
         doc_id: str | None = None) -> TraceEvent:
    """Write the audit row and return the trace event for one finished step."""
    ms = int((time.perf_counter() - t0) * 1000)
    status = "error" if "error" in output else "ok"
    audit.record(state.get("claim_id"), actor=agent, event="agent_completed", status=status,
                 detail=summarize(output), doc_id=doc_id, duration_ms=ms)
    return {"agent": agent, "status": status, "started_at": started, "duration_ms": ms,
            "note": str(output.get("error", ""))[:200]}


def claim_node(agent: str, writes: str) -> Callable:
    def decorator(fn: Callable[[ClaimState], dict[str, Any]]) -> Callable:
        @functools.wraps(fn)
        def wrapper(state: ClaimState) -> dict[str, Any]:
            started, t0 = audit.now(), time.perf_counter()
            try:
                output = fn(state) or {}
            except Exception as exc:                  # one bad agent must not kill the claim
                output = {"error": f"{type(exc).__name__}: {exc}"}
            event = emit(state, agent, started, t0, output)
            return {writes: output, "trace": [event], "status": agent.upper()}
        return wrapper
    return decorator
