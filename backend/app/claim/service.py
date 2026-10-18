"""
Claim lifecycle: queue, run the claim graph, persist, human review.

Status of a claim (claim_bundle.status):
    QUEUED -> PROCESSING -> APPROVED | REJECTED | NEEDS_REVIEW | FAILED
    NEEDS_REVIEW -> REVIEWED                      (ops staff decide; the paused run resumes)

An ESCALATE run pauses at the human_review node (LangGraph interrupt) and its state is kept in
the checkpoint store under its thread id. The reviewer's decision resumes that same run.
    any -> QUEUED                                 (re-run, e.g. after correcting a document type)

One worker thread runs claims in submission order, so documents are read one at a time
and the OCR engine and database are never hit by bursts.
"""

from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from app import audit, config, db
from langgraph.types import Command

from app.claim.graph import get_claim_graph
from app.claim.state import ClaimState

log = logging.getLogger("medishield.claims")
_executor = ThreadPoolExecutor(max_workers=config.PIPELINE_WORKERS, thread_name_prefix="claim")

STATUS_BY_DECISION = {"APPROVE": "APPROVED", "REJECT": "REJECTED", "ESCALATE": "NEEDS_REVIEW"}
RESULT_KEYS = ("plan", "kyc", "claims", "policy", "fraud", "decision", "trace")


def run_config(thread_id: str, pages: int = 0) -> dict:
    # Three graph steps per page (preprocess, classify, extract), plus the fixed agents.
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 3 * pages + 30}


def run_claim_graph(claim_id: str, title: str, documents: list[dict], thread_id: str | None = None) -> dict:
    """Run the graph to the end, or until it pauses for a human. Returns the state; the
    returned state has "__interrupt__" when the run is waiting at human_review."""
    state: ClaimState = {
        "claim_id": claim_id, "title": title,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "documents": documents, "docs": [], "trace": [], "status": "RECEIVED",
    }
    return get_claim_graph().invoke(state, config=run_config(thread_id or f"{claim_id}:{uuid.uuid4().hex[:8]}",
                                                              len(documents)))


def waiting_for_human(thread_id: str) -> bool:
    return "human_review" in (get_claim_graph().get_state(run_config(thread_id)).next or ())


def _documents_of(claim_id: str) -> list[dict]:
    out = []
    for row in db.bundle_documents(claim_id):
        override = (row.get("override") or {}).get("doc_type")
        out.append({"doc_id": row["case_id"], "file_path": row["result"]["file_path"],
                    "file_name": row["file_name"], "doc_type_override": override or None})
    return out


def _persist_documents(claim_id: str, state: dict) -> None:
    rows = {r["case_id"]: r for r in db.bundle_documents(claim_id)}
    for doc in state.get("docs") or []:
        row = rows.get(doc["doc_id"])
        if row is None:
            continue
        db.save_case({**row, "doc_type": doc.get("doc_type"),
                      "status": "UNREADABLE" if doc.get("error") else "INGESTED",
                      "decision": None, "confidence": doc.get("read_confidence"), "needs_review": False,
                      "result": {"file_path": row["result"]["file_path"], "ingest": doc}})


def _persist_history(claim_id: str, state: dict) -> None:
    """Store the claim and its stays so later claims are checked against them."""
    claims = state.get("claims") or {}
    if "error" in claims:
        return
    member_id = (state.get("fraud") or {}).get("member_id") or ((state.get("kyc") or {}).get("member") or {}).get("member_id")
    claim = claims.get("claim")
    if claim:
        f = claim["extracted_fields"]
        db.save_claim(claim_id, {**f, "member_id": member_id})
    events = []
    for c in claims.get("clinical") or []:
        f = c["extracted_fields"]
        events.append({"admission_date": f.get("admission_date"), "discharge_date": f.get("discharge_date"),
                       "prescription_date": f.get("prescription_date"),
                       "diagnosis": ", ".join(f.get("icd10_codes") or [])})
    if claim and claim["extracted_fields"].get("admission_date"):
        f = claim["extracted_fields"]
        events.append({"admission_date": f.get("admission_date"), "discharge_date": f.get("discharge_date")})
    db.save_claim_events(claim_id, member_id, [e for e in events if any(e.values())])


def result_of(state: dict) -> dict[str, Any]:
    result = {k: state.get(k) for k in RESULT_KEYS}
    result["documents"] = list(state.get("docs") or [])          # with the preprocessor's page text
    result["review"] = state.get("review")
    return result


def process_claim(claim_id: str) -> None:
    bundle = db.get_bundle(claim_id, with_result=False)
    documents = _documents_of(claim_id)
    db.update_bundle(claim_id, "PROCESSING", keep_result=True)
    audit.record(claim_id, "orchestrator", "claim_started", detail={"documents": len(documents)})
    thread_id = f"{claim_id}:{uuid.uuid4().hex[:8]}"            # one checkpoint thread per run
    try:
        state = run_claim_graph(claim_id, bundle["title"], documents, thread_id=thread_id)
        _persist_documents(claim_id, state)
        _persist_history(claim_id, state)
        out = state.get("decision") or {}
        decision = out.get("decision")
        if decision is None:                       # the decision node itself failed
            raise RuntimeError(out.get("error") or "no decision produced")
        paused = waiting_for_human(thread_id)
        result = {**result_of(state), "thread_id": thread_id, "awaiting_human": paused}
        db.update_bundle(claim_id, STATUS_BY_DECISION[decision], decision=decision,
                         confidence=out.get("confidence"), needs_review=decision == "ESCALATE",
                         result=result)
        audit.record(claim_id, "orchestrator", "claim_decided",
                     detail={"decision": decision, "confidence": out.get("confidence"),
                             "justification": out.get("justification")})
        if paused:
            audit.record(claim_id, "orchestrator", "paused_for_human_review", detail={"thread_id": thread_id})
    except Exception as exc:                       # a claim must end FAILED, never stay PROCESSING
        log.exception("claim %s failed", claim_id)
        db.update_bundle(claim_id, "FAILED", decision="ESCALATE", needs_review=True,
                         result={"error": f"{type(exc).__name__}: {exc}"})
        audit.record(claim_id, "orchestrator", "claim_failed", status="error",
                     detail={"error": f"{type(exc).__name__}: {exc}"})


def submit_claim(title: str, pages: list[tuple[str, str, str | None]], created_by: str,
                 patient_id: str | None = None) -> str:
    """pages: (stored path, display name, document type or None to classify). Returns the claim id."""
    claim_id = str(uuid.uuid4())
    db.insert_bundle(claim_id, title, patient_id, created_by=created_by)
    for path, name, doc_type in pages:
        db.save_case({"bundle_id": claim_id, "case_id": str(uuid.uuid4()), "file_name": name,
                      "doc_type": doc_type, "status": "QUEUED", "decision": None, "confidence": None,
                      "needs_review": False, "result": {"file_path": path},
                      "override": {"doc_type": doc_type} if doc_type else None,
                      "patient_id": patient_id, "created_by": created_by})
    db.update_bundle(claim_id, "QUEUED", updated_by=created_by)
    audit.record(claim_id, created_by, "claim_submitted",
                 detail={"documents": len(pages), "files": [name for _, name, _ in pages]})
    _executor.submit(process_claim, claim_id)
    return claim_id


def rerun_claim(claim_id: str, user: str, doc_types: dict[str, str] | None = None) -> None:
    """Queue the claim again. doc_types {doc_id: type} corrects the classifier first."""
    rows = {r["case_id"]: r for r in db.bundle_documents(claim_id)}
    for doc_id, doc_type in (doc_types or {}).items():
        row = rows.get(doc_id)
        if row is None:
            raise KeyError(doc_id)
        db.save_case({**row, "override": {**(row.get("override") or {}), "doc_type": doc_type},
                      "updated_by": user})
        audit.record(claim_id, user, "document_reclassified", doc_id=doc_id,
                     detail={"file_name": row["file_name"], "from": row.get("doc_type"), "to": doc_type})
    db.update_bundle(claim_id, "QUEUED", keep_result=True, updated_by=user)
    audit.record(claim_id, user, "claim_rerun_requested")
    _executor.submit(process_claim, claim_id)


def override_decision(claim_id: str, decision: str, note: str, user: str) -> None:
    """Ops staff decide a claim. An escalated claim whose run is paused at human_review is
    resumed from its checkpoint with this decision; any other finished claim gets the
    decision recorded as an override. Either way the agents' decision is kept."""
    bundle = db.get_bundle(claim_id)
    original = bundle.get("decision")
    result = bundle.get("result") or {}
    thread_id = result.get("thread_id")
    if thread_id and waiting_for_human(thread_id):
        state = get_claim_graph().invoke(Command(resume={"decision": decision, "note": note, "reviewer": user}),
                                         config=run_config(thread_id))
        review = state.get("review") or {}
        audit.record(claim_id, user, "human_review_resumed",
                     detail={"from": original, "decision": decision, "note": note, "thread_id": thread_id})
        db.update_bundle(claim_id, "REVIEWED", decision=decision, confidence=bundle.get("confidence"),
                         needs_review=False, override=review,
                         result={**result, "review": review, "awaiting_human": False, "trace": state.get("trace")},
                         updated_by=user)
        return
    override = {"decision": decision, "note": note, "reviewer": user,
                "reviewed_at": audit.now(), "original_decision": original}
    db.update_bundle(claim_id, "REVIEWED", decision=decision, confidence=bundle.get("confidence"),
                     needs_review=False, override=override, keep_result=True, updated_by=user)
    audit.record(claim_id, user, "human_override",
                 detail={"from": original, "decision": decision, "note": note})


def recover_interrupted_claims() -> int:
    """Claims still QUEUED or PROCESSING at startup were lost with the previous process: queue them again."""
    stuck = db.list_bundles(status="PROCESSING") + db.list_bundles(status="QUEUED")
    for b in stuck:
        audit.record(b["bundle_id"], "system", "claim_requeued_after_restart")
        _executor.submit(process_claim, b["bundle_id"])
    return len(stuck)
