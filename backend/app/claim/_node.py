"""
Wrapper for every claim-graph agent: timing, a trace event, audit rows, error capture.
A failing agent writes {"error": ...} as its output instead of crashing the claim, and
the orchestrator turns that into ESCALATE (or the claim is put on hold for a person).

Each agent writes two audit rows: "agent_started" when it begins (so the UI knows exactly
which agent is running and for how long) and "agent_completed" when it ends, with an
investigation note: what the agent worked on, how long it took and what it returned.
"""

from __future__ import annotations

import functools
import time
from contextvars import ContextVar
from typing import Any, Callable

from app import audit, llm
from app.claim.state import ClaimState, TraceEvent

AUDIT_KEYS = ("error", "decision", "confidence", "kyc_passed", "flags", "fail_reasons", "covered", "exclusions",
              "needs_review", "review_reasons", "fraud_score", "risk_level", "tags", "agents", "justification",
              "reasons", "unknown_documents", "missing_required", "document_counts", "plan",
              "doc_type", "classified_as", "file_name", "read_confidence", "classifier_confidence",
              "classifier_source", "image_quality", "is_blurry", "blur_variance", "text_chars",
              "fields_extracted", "fields_marked_missing", "skipped", "action", "note", "step", "text_quality",
              "insurance_cards", "is_duplicate", "duplicate_of", "service_date", "claim_amount", "checked")


class ClaimCancelled(BaseException):
    """A person stopped the claim. Raised before an agent starts and, while a model call is in
    flight, from inside it. A BaseException, so an agent's `except Exception` cannot turn the
    stop into an ordinary agent error."""


# Set by the claim service: claim_id -> True when a person asked to stop that claim.
_cancel_check: Callable[[str | None], bool] = lambda _claim_id: False

# The claim this thread is working on. Set by the claim service around a run; LangGraph copies
# it into the threads that run parallel agents, and app.llm into its request threads.
current_claim: ContextVar[str | None] = ContextVar("current_claim", default=None)


def set_cancel_check(check: Callable[[str | None], bool]) -> None:
    global _cancel_check
    _cancel_check = check


def check_cancelled(state: ClaimState) -> None:
    if _cancel_check(state.get("claim_id")):
        raise ClaimCancelled(state.get("claim_id"))


def _cancel_current() -> None:
    claim_id = current_claim.get()
    if claim_id is not None and _cancel_check(claim_id):
        raise ClaimCancelled(claim_id)


llm.set_cancel_hook(_cancel_current)                  # a stop also ends a model call in flight


def fmt_duration(ms: int) -> str:
    if ms < 1000:
        return f"{ms} ms"
    s = ms / 1000
    if s < 60:
        return f"{s:.1f} s"
    return f"{int(s // 60)}m {int(s % 60):02d}s"


def _pct(v: Any) -> str:
    try:
        return f"{float(v):.0%}"
    except (TypeError, ValueError):
        return "n/a"


def _join(items: Any, empty: str = "none") -> str:
    items = [str(i) for i in (items or [])]
    return ", ".join(items) if items else empty


def describe(agent: str, out: dict) -> str:
    """What the agent returned, in one readable sentence for the audit trail."""
    if out.get("skipped"):
        return f"nothing, skipped because an earlier step failed on this page ({out.get('error')})"
    if "error" in out:
        return f"FAILED: {out['error']}"
    if agent == "classify":
        blur = (f"; image {out.get('image_quality', 'clear')}, blur variance {out.get('blur_variance')} "
                f"({'blurry' if out.get('is_blurry') else 'sharp'})")
        if out.get("classifier_source") == "human_override":
            return f"type {out.get('doc_type')} set by a reviewer (classifier skipped)" + blur
        raw = out.get("classified_as")
        below = raw and raw != out.get("doc_type")
        return (f"type {out.get('doc_type')} at {_pct(out.get('classifier_confidence'))} confidence"
                + (f" (model said {raw}, below the confidence threshold)" if below else "") + blur)
    if agent == "preprocess":
        return f"{out.get('text_chars', 0):,} characters of text (image {out.get('text_quality', 'clear')})"
    if agent == "extract":
        return (f"{out.get('text_chars', 0):,} characters of text (image {out.get('text_quality', 'clear')}); "
                f"{len(out.get('fields_extracted') or [])} field(s) read: {_join(out.get('fields_extracted'))}; "
                f"marked missing on the page: {_join(out.get('fields_marked_missing'))}")
    if agent == "plan":
        counts = out.get("document_counts") or {}
        return (f"agents to run: {_join(out.get('agents'))}; pages by type: "
                f"{_join(f'{k} x{v}' for k, v in counts.items())}; unknown: {_join(out.get('unknown_documents'))}; "
                f"missing required: {_join(out.get('missing_required'))}")
    if agent == "kyc":
        member = (out.get("member") or {}).get("member_id")
        return (f"KYC {'passed' if out.get('kyc_passed') else 'failed'}; member {member or 'not found'}; "
                + (f"needs review: {_join(out.get('review_reasons'))}; " if out.get("needs_review") else "")
                + f"flags: {_join(out.get('flags'))}; confidence {_pct(out.get('confidence'))}")
    if agent == "claims":
        return f"{out.get('summary', '')}; confidence {_pct(out.get('confidence'))}"
    if agent == "policy":
        return (f"{'covered' if out.get('covered') else 'NOT covered'} under {out.get('plan') or 'unknown plan'}; "
                f"exclusions: {_join(out.get('exclusions'))}; confidence {_pct(out.get('confidence'))}")
    if agent == "fraud":
        return (f"fraud score {out.get('fraud_score', 0)} ({out.get('risk_level', '-')}); "
                f"tags: {_join(out.get('tags'))}")
    if agent == "duplicate_check":
        return out.get("summary", "")
    if agent == "decide":
        return (f"{out.get('decision')} at {_pct(out.get('confidence'))} confidence; "
                f"reasons: {'; '.join(out.get('reasons') or []) or 'none'}")
    if agent == "human_review":
        return f"reviewer decided {out.get('decision')}: {out.get('justification') or ''}"
    if agent == "ingest_hold":
        return f"person chose to {out.get('action')}" + (f": {out['note']}" if out.get("note") else "")
    return ""


def summarize(output: Any) -> dict[str, Any]:
    """The small part of an agent output worth keeping in the audit row."""
    if not isinstance(output, dict):
        return {}
    out = {k: output[k] for k in AUDIT_KEYS if k in output}
    if isinstance(output.get("member"), dict):
        out["member_id"] = output["member"].get("member_id")
    return out


def started(state: ClaimState, agent: str, doc_id: str | None = None, file_name: str | None = None) -> None:
    detail = {"file_name": file_name} if file_name else {}
    audit.record(state.get("claim_id"), actor=agent, event="agent_started", detail=detail, doc_id=doc_id)


def emit(state: ClaimState, agent: str, started_at: str, t0: float, output: dict,
         doc_id: str | None = None, returned: dict | None = None) -> TraceEvent:
    """Write the audit row and return the trace event for one finished step. `returned` is
    the agent's own output when `output` also carries earlier steps' fields (page steps)."""
    ms = int((time.perf_counter() - t0) * 1000)
    status = "error" if "error" in output else "ok"
    own = returned if returned is not None else output
    if "error" in output:
        own = {**own, "error": output["error"]}
    subject = f" on {output['file_name']}" if output.get("file_name") else ""
    note = f"{agent}{subject} took {fmt_duration(ms)} and returned: {describe(agent, own)}"
    detail = {**summarize(own), "note": note}
    if output.get("file_name"):
        detail["file_name"] = output["file_name"]
    audit.record(state.get("claim_id"), actor=agent, event="agent_completed", status=status,
                 detail=detail, doc_id=doc_id, duration_ms=ms)
    return {"agent": agent, "status": status, "started_at": started_at, "duration_ms": ms,
            "note": str(output.get("error", ""))[:200]}


def claim_node(agent: str, writes: str) -> Callable:
    def decorator(fn: Callable[[ClaimState], dict[str, Any]]) -> Callable:
        @functools.wraps(fn)
        def wrapper(state: ClaimState) -> dict[str, Any]:
            check_cancelled(state)
            started(state, agent)
            started_at, t0 = audit.now(), time.perf_counter()
            try:
                output = fn(state) or {}
            except Exception as exc:                  # one bad agent must not kill the claim
                output = {"error": f"{type(exc).__name__}: {exc}"}
            event = emit(state, agent, started_at, t0, output)
            return {writes: output, "trace": [event], "status": agent.upper()}
        return wrapper
    return decorator
