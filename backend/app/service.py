"""
Case lifecycle: run the graph, persist the facts, and handle the human review
(HIL) step.

Human review does two things:
- wrong document type -> re-run the graph with doc_type_override (the classifier
  honours it), so every downstream agent sees the corrected type;
- decision only -> record the reviewer's decision on the case as an override.
"""

from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import config, db
from app.graph import get_graph
from app.state import CaseState

STATUS_BY_DECISION = {"APPROVE": "APPROVED", "REJECT": "REJECTED", "ESCALATE": "NEEDS_REVIEW"}


def run_pipeline(file_path: str, file_name: str, case_id: str,
                 override: str | None = None) -> dict[str, Any]:
    state: CaseState = {
        "case_id": case_id,
        "file_path": file_path,
        "file_name": file_name,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "doc_type_override": override,
        "trace": [],
        "status": "RECEIVED",
    }
    return get_graph().invoke(state)


def _persist_facts(case_id: str, state: dict) -> None:
    claims = state.get("claims") or {}
    if "error" in claims:
        return
    member_id = (state.get("fraud") or {}).get("member_id")
    extracted = claims.get("extracted_fields") or {}
    if claims.get("kind") == "CLAIM":
        db.save_claim(case_id, {**extracted, "member_id": member_id})
    elif claims.get("kind") == "CLINICAL":
        db.save_clinical_event(case_id, {**extracted, "member_id": member_id})


def _record(case_id: str, file_name: str, file_path: str, state: dict,
            override: dict | None = None, status: str | None = None) -> dict:
    orchestrator = state.get("orchestrator") or {}
    decision = orchestrator.get("decision")
    doc_type = (state.get("classifier") or {}).get("doc_type")
    result = {k: state.get(k) for k in
              ("preprocessor", "classifier", "kyc", "claims", "policy", "fraud",
               "orchestrator", "trace")}
    result["file_path"] = file_path
    return {
        "case_id": case_id,
        "file_name": file_name,
        "doc_type": doc_type,
        "status": status or STATUS_BY_DECISION.get(decision, "ERROR"),
        "decision": decision,
        "confidence": orchestrator.get("confidence"),
        "needs_review": bool(orchestrator.get("needs_review", True)),
        "result": result,
        "override": override,
    }


def process_upload(file_path: str, file_name: str, doc_type: str | None = None) -> dict:
    case_id = str(uuid.uuid4())
    state = run_pipeline(file_path, file_name, case_id, override=doc_type)
    _persist_facts(case_id, state)
    record = _record(case_id, file_name, file_path, state)
    db.save_case(record)
    return db.get_case(case_id)


def review_case(case_id: str, doc_type: str | None = None, decision: str | None = None,
                note: str | None = None, user: str | None = None) -> dict:
    existing = db.get_case(case_id)
    if existing is None:
        raise KeyError(case_id)
    file_path = existing["result"]["file_path"]
    override = {"doc_type": doc_type, "decision": decision, "note": note}

    if doc_type:
        state = run_pipeline(file_path, existing["file_name"], case_id, override=doc_type)
        _persist_facts(case_id, state)
        record = _record(case_id, existing["file_name"], file_path, state,
                         override=override, status="REVIEWED")
    else:
        final = decision or existing["decision"]
        result = dict(existing["result"])
        result["orchestrator"] = {
            **(result.get("orchestrator") or {}),
            "decision": final,
            "justification": f"reviewer decision: {note or 'no note'}",
            "needs_review": False,
        }
        record = {**existing, "result": result, "override": override, "status": "REVIEWED",
                  "needs_review": False, "decision": final}

    if decision and doc_type:
        # A reviewer who also sets a decision wins over the re-run's decision.
        record["decision"] = decision
        record["result"]["orchestrator"]["decision"] = decision
    record["updated_by"] = user
    db.save_case(record)
    return db.get_case(case_id)


def document_path(case_id: str) -> Path | None:
    case = db.get_case(case_id)
    if case is None:
        return None
    return Path(case["result"]["file_path"])


# ---------------------------------------------------------------------------
# Background processing. Uploads return at once with status PROCESSING. One
# worker runs the pipeline, so documents are processed in order and Groq is
# not hit by parallel bursts.
# ---------------------------------------------------------------------------

log = logging.getLogger("medishield.jobs")
_executor = ThreadPoolExecutor(max_workers=config.PIPELINE_WORKERS, thread_name_prefix="pipeline")


def _processing_record(case_id: str, file_name: str, file_path: str, doc_type: str | None,
                       patient_id: str | None = None, created_by: str | None = None,
                       bundle_id: str | None = None) -> dict:
    return {
        "bundle_id": bundle_id,
        "case_id": case_id,
        "file_name": file_name,
        "doc_type": doc_type,
        "status": "PROCESSING",
        "decision": None,
        "confidence": None,
        "needs_review": False,
        "result": {"file_path": file_path},
        "override": None,
        "patient_id": patient_id,
        "created_by": created_by,
    }


def _run_job(case_id: str, file_name: str, file_path: str, doc_type: str | None,
             patient_id: str | None = None, created_by: str | None = None,
             bundle_id: str | None = None) -> None:
    try:
        state = run_pipeline(file_path, file_name, case_id, override=doc_type)
        _persist_facts(case_id, state)
        db.save_case({**_record(case_id, file_name, file_path, state),
                      "patient_id": patient_id, "created_by": created_by, "bundle_id": bundle_id})
    except Exception as exc:                       # the case must end as FAILED, never stay PROCESSING
        log.exception("pipeline failed for %s", case_id)
        db.save_case({**_processing_record(case_id, file_name, file_path, doc_type, patient_id,
                                           created_by, bundle_id),
                      "status": "FAILED",
                      "result": {"file_path": file_path,
                                 "error": f"{type(exc).__name__}: {exc}"}})


def enqueue_upload(file_path: str, file_name: str, doc_type: str | None = None,
                   patient_id: str | None = None, created_by: str | None = None,
                   bundle_id: str | None = None) -> str:
    case_id = str(uuid.uuid4())
    db.save_case(_processing_record(case_id, file_name, file_path, doc_type, patient_id,
                                    created_by, bundle_id))
    _executor.submit(_run_job, case_id, file_name, file_path, doc_type, patient_id, created_by, bundle_id)
    return case_id


def retry_case(case_id: str, user: str | None = None) -> dict | None:
    existing = db.get_case(case_id)
    if existing is None:
        return None
    file_path = existing["result"]["file_path"]
    patient_id, created_by = existing.get("patient_id"), existing.get("created_by")
    bundle_id = existing.get("bundle_id")
    db.save_case({**_processing_record(case_id, existing["file_name"], file_path,
                                       existing["doc_type"], patient_id, created_by, bundle_id),
                  "updated_by": user})
    _executor.submit(_run_job, case_id, existing["file_name"], file_path, existing["doc_type"],
                     patient_id, created_by, bundle_id)
    return db.get_case(case_id)


def recover_interrupted() -> int:
    """Jobs still PROCESSING at startup were lost with the previous process. Mark them FAILED."""
    stuck = db.list_cases(decision=None, status="PROCESSING")
    for case in stuck:
        record = db.get_case(case["case_id"])
        record["status"] = "FAILED"
        record["result"]["error"] = "interrupted: the server restarted before processing finished"
        db.save_case(record)
    return len(stuck)
