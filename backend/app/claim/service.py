"""
Claim lifecycle: queue, run the claim graph, persist, human review.

Status of a claim (claim_bundle.status):
    QUEUED -> PROCESSING -> APPROVED | REJECTED | NEEDS_REVIEW | ON_HOLD | STOPPED | FAILED
    NEEDS_REVIEW -> APPROVED | REJECTED           ops staff decide; the paused run resumes
    NEEDS_REVIEW -> NEEDS_REVIEW                  "keep escalated": the note is recorded and the
                                                  run stays paused at the checkpoint
    ON_HOLD                                       a page is UNKNOWN, blurry or failed at classification
                                                  (checked for every page before any is read), a page
                                                  could not be read, or an agent failed: a person
                                                  continues, stops or re-uploads
    PROCESSING -> NEEDS_REVIEW                    a duplicate: the patient already has a stored claim with
                                                  the same date of service and amount (app.claim.duplicate);
                                                  the agents are skipped, it is escalated to a person and not
                                                  stored again for later checks
    QUEUED | PROCESSING | ON_HOLD | NEEDS_REVIEW -> STOPPED
                                                  a person stopped it. A running claim stops at once:
                                                  the model call in flight is abandoned and no further
                                                  agent starts
    STOPPED -> PROCESSING                         resumed: the run continues from its checkpoint (the
                                                  last finished step); a claim that was waiting for a
                                                  person goes back to waiting (ON_HOLD / NEEDS_REVIEW)

An ESCALATE run pauses at the human_review node (LangGraph interrupt) and its state is kept in
the checkpoint store under its thread id. The reviewer's decision resumes that same run.
    any -> QUEUED                                 (re-run, e.g. after correcting a document type)

One worker thread runs claims in submission order, so documents are read one at a time
and the vision model and database are never hit by bursts. Only one claim may be queued or
processing at a time: new uploads, re-runs and re-uploads are refused until it finishes.
"""

from __future__ import annotations

import logging
import pathlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from app import audit, config, db
from langgraph.types import Command

from app.claim import intake
from app.claim._node import ClaimCancelled, current_claim, set_cancel_check
from app.claim.graph import get_claim_graph
from app.claim.hold import problem_pages, stage_of
from app.claim.state import ClaimState

log = logging.getLogger("medishield.claims")
_executor = ThreadPoolExecutor(max_workers=config.PIPELINE_WORKERS, thread_name_prefix="claim")

STATUS_BY_DECISION = {"APPROVE": "APPROVED", "REJECT": "REJECTED", "ESCALATE": "NEEDS_REVIEW"}
RESULT_KEYS = ("plan", "kyc", "claims", "duplicate", "policy", "fraud", "decision", "trace", "hold")
RUNNING = ("QUEUED", "PROCESSING")

# Claims a person stopped while their run was queued or in flight. The run checks this before
# each agent starts and every half second during a model call; the run removes it when it ends.
_stop_requested: set[str] = set()
set_cancel_check(lambda claim_id: claim_id in _stop_requested)

# claim_id -> the checkpoint thread of its run in flight, so a stop knows where to resume from.
_running: dict[str, str] = {}


class ClaimBusy(Exception):
    """Another claim is queued or processing; only one runs at a time."""


def active_claim(exclude: str | None = None) -> dict | None:
    """The claim that is queued or processing now, if any."""
    for status in RUNNING:
        for b in db.list_bundles(status=status, limit=5):
            if b["bundle_id"] != exclude:
                return b
    return None


def ensure_idle(exclude: str | None = None) -> None:
    busy = active_claim(exclude)
    if busy:
        raise ClaimBusy(f"claim '{busy['title']}' is {busy['status'].lower()}; only one claim runs at a time. "
                        "Wait for it to finish or stop it.")


def run_config(thread_id: str, pages: int = 0) -> dict:
    # Two graph steps per page (classify, then preprocess + extract), plus the fixed agents.
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 3 * pages + 30}


def run_claim_graph(claim_id: str, title: str, documents: list[dict], thread_id: str | None = None) -> dict:
    """Run the graph to the end, or until it pauses for a human. Returns the state; the
    returned state has "__interrupt__" when the run is waiting at human_review."""
    state: ClaimState = {
        "claim_id": claim_id, "title": title,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "documents": documents, "classified": [], "docs": [], "trace": [], "status": "RECEIVED",
    }
    return get_claim_graph().invoke(state, config=run_config(thread_id or f"{claim_id}:{uuid.uuid4().hex[:8]}",
                                                              len(documents)))


def paused_at(thread_id: str) -> tuple[str, ...]:
    return tuple(get_claim_graph().get_state(run_config(thread_id)).next or ())


def waiting_for_human(thread_id: str) -> bool:
    return "human_review" in paused_at(thread_id)


def waiting_on_hold(thread_id: str) -> bool:
    return "ingest_hold" in paused_at(thread_id)


def _documents_of(claim_id: str) -> list[dict]:
    out = []
    for row in db.bundle_documents(claim_id):
        override = (row.get("override") or {}).get("doc_type")
        out.append({"doc_id": row["case_id"], "file_path": row["result"]["file_path"],
                    "file_name": row["file_name"], "doc_type_override": override or None})
    return out


def pages_of(state: dict) -> list[dict]:
    """Every page as far as it got: read pages, then pages only classified so far."""
    docs = list(state.get("docs") or [])
    read = {d["doc_id"] for d in docs}
    return docs + [p for p in state.get("classified") or [] if p["doc_id"] not in read]


def _persist_documents(claim_id: str, state: dict) -> None:
    rows = {r["case_id"]: r for r in db.bundle_documents(claim_id)}
    read = {d["doc_id"] for d in state.get("docs") or []}
    for doc in pages_of(state):
        row = rows.get(doc["doc_id"])
        if row is None:
            continue
        db.save_case({**row, "doc_type": doc.get("doc_type"),
                      "status": "UNREADABLE" if doc.get("error") else "BLURRY" if doc.get("is_blurry")
                      else "INGESTED" if doc["doc_id"] in read else "CLASSIFIED",
                      "decision": None, "confidence": doc.get("read_confidence"), "needs_review": False,
                      "result": {"file_path": row["result"]["file_path"], "ingest": doc}})


def link_patient(claim_id: str, docs: list[dict], kyc_member: dict | None = None) -> str | None:
    """Link a claim with no patient to the member its pages name (registering the patient from
    the member record if needed). The patient id, or None when no member could be found."""
    bundle = db.get_bundle(claim_id, with_result=False)
    if bundle is None or bundle.get("patient_id"):
        return bundle and bundle.get("patient_id")
    member, found_by = intake.identify_member(docs, kyc_member)
    if member is None:
        return None
    patient_id = intake.register_patient(member["member_id"], bundle.get("created_by") or "system")
    if patient_id and db.link_bundle_patient(claim_id, patient_id):
        db.add_case_patient(claim_id, member["member_id"])
        audit.record(claim_id, "orchestrator", "patient_linked",
                     detail={"member_id": member["member_id"], "full_name": member.get("full_name"),
                             "patient_id": patient_id, "found_by": found_by})
    return patient_id


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
    result["documents"] = pages_of(state)                        # with the page text once read
    result["review"] = state.get("review")
    return result


def _agent_errors(state: dict) -> list[str]:
    out = []
    for key in ("plan", "kyc", "claims", "policy", "fraud"):
        v = state.get(key)
        if isinstance(v, dict) and "error" in v:
            out.append(f"{key} agent failed: {v['error']}")
    return out


def _finish(claim_id: str, thread_id: str, state: dict) -> None:
    """Store the outcome of a run that ended or paused."""
    _persist_documents(claim_id, state)
    try:
        link_patient(claim_id, pages_of(state), (state.get("kyc") or {}).get("member"))
    except Exception:                              # the patient link is a convenience; never fail the claim on it
        log.exception("claim %s: patient not linked", claim_id)
    if waiting_on_hold(thread_id):                 # a page needs a person before the run goes on
        pages = problem_pages(state)
        step = stage_of(state)
        hold = {"stage": "ingest", "step": step, "pages": pages,
                "reasons": [f"{p['file_name']}: {p['reason']}" for p in pages]}
        db.update_bundle(claim_id, "ON_HOLD", needs_review=True,
                         result={**result_of(state), "thread_id": thread_id, "awaiting_human": True,
                                 "awaiting": "ingest_hold", "hold": hold})
        audit.record(claim_id, "orchestrator", "claim_on_hold",
                     detail={"stage": "ingest", "step": step, "reasons": hold["reasons"], "thread_id": thread_id,
                             "note": ("Not every page could be classified (unknown, blurry or failed), so no page "
                                      "was read. " if step == "classify" else
                                      "A page could not be read, so the claim agents did not run. ")
                                     + "A person must continue, stop, or re-upload the page(s)."})
        return
    if (state.get("hold") or {}).get("action") == "stop":
        db.update_bundle(claim_id, "STOPPED", needs_review=False,
                         result={**result_of(state), "thread_id": thread_id, "awaiting_human": False})
        audit.record(claim_id, "orchestrator", "claim_stopped", detail={"note": "stopped at the hold"})
        return
    dup = state.get("duplicate") or {}
    if dup.get("is_duplicate"):                    # a repeat of a stored claim: decide escalates it to a person
        audit.record(claim_id, "orchestrator", "claim_duplicate",
                     detail={"duplicate_of": dup.get("duplicate_of"), "member_id": dup.get("member_id"),
                             "service_date": dup.get("service_date"), "claim_amount": dup.get("claim_amount"),
                             "note": dup.get("summary")})
    try:
        if not dup.get("is_duplicate"):            # the original is already stored; a repeat adds nothing
            _persist_history(claim_id, state)
    except Exception as exc:                       # history feeds later fraud checks; it must not fail this claim
        log.exception("claim %s: history not stored", claim_id)
        audit.record(claim_id, "orchestrator", "history_not_stored", status="error",
                     detail={"error": f"{type(exc).__name__}: {exc}",
                             "note": "The decision stands; this claim's facts were not saved for later duplicate/readmission checks."})
    out = state.get("decision") or {}
    decision = out.get("decision")
    if decision is None:                           # the decision node itself failed
        raise RuntimeError(out.get("error") or "no decision produced")
    paused = waiting_for_human(thread_id)
    result = {**result_of(state), "thread_id": thread_id, "awaiting_human": paused,
              "awaiting": "human_review" if paused else None}
    errors = _agent_errors(state)
    status = STATUS_BY_DECISION[decision]
    if errors:                                     # a step did not complete: hold, do not just escalate
        status = "ON_HOLD"
        result["hold"] = {"stage": "agents", "reasons": errors}
    db.update_bundle(claim_id, status, decision=decision,
                     confidence=out.get("confidence"), needs_review=decision == "ESCALATE" or bool(errors),
                     result=result)
    audit.record(claim_id, "orchestrator", "claim_decided",
                 detail={"decision": decision, "confidence": out.get("confidence"),
                         "justification": out.get("justification")})
    if errors:
        audit.record(claim_id, "orchestrator", "claim_on_hold",
                     detail={"stage": "agents", "reasons": errors,
                             "note": "An agent did not complete. A person must re-run, stop or decide."})
    if paused:
        audit.record(claim_id, "orchestrator", "paused_for_human_review", detail={"thread_id": thread_id})


def _stopped_mid_run(claim_id: str, thread_id: str) -> None:
    """The run ended because a person stopped the claim. Store what it finished (stop_claim
    already set STOPPED) and where it can resume from."""
    state = get_claim_graph().get_state(run_config(thread_id)).values or {}
    _persist_documents(claim_id, state)
    bundle = db.get_bundle(claim_id)
    if bundle and bundle["status"] == "STOPPED":
        resume = {**((bundle.get("result") or {}).get("resume") or {}), "thread_id": thread_id}
        db.update_bundle(claim_id, "STOPPED", needs_review=False,
                         result={**result_of(state), "thread_id": thread_id, "awaiting_human": False, "resume": resume})
    audit.record(claim_id, "orchestrator", "run_cancelled",
                 detail={"thread_id": thread_id, "resume_at": list(paused_at(thread_id)),
                         "note": "The run ended at once. Resume continues from the last finished step."})


def _run(claim_id: str, thread_id: str, call) -> None:
    """Run (or resume) a claim's graph and store the outcome. Never leaves it PROCESSING."""
    _running[claim_id] = thread_id
    scope = current_claim.set(claim_id)            # lets a stop end a model call in flight
    try:
        _finish(claim_id, thread_id, call())
    except ClaimCancelled:
        _stopped_mid_run(claim_id, thread_id)
    except Exception as exc:                       # a claim must end FAILED, never stay PROCESSING
        log.exception("claim %s failed", claim_id)
        db.update_bundle(claim_id, "FAILED", decision="ESCALATE", needs_review=True,
                         result={"error": f"{type(exc).__name__}: {exc}"})
        audit.record(claim_id, "orchestrator", "claim_failed", status="error",
                     detail={"error": f"{type(exc).__name__}: {exc}"})
    finally:
        current_claim.reset(scope)
        _running.pop(claim_id, None)
        _stop_requested.discard(claim_id)


def process_claim(claim_id: str) -> None:
    bundle = db.get_bundle(claim_id, with_result=False)
    if claim_id in _stop_requested or bundle is None or bundle["status"] != "QUEUED":   # stopped while queued
        _stop_requested.discard(claim_id)
        return
    documents = _documents_of(claim_id)
    db.update_bundle(claim_id, "PROCESSING", keep_result=True)
    audit.record(claim_id, "orchestrator", "claim_started", detail={"documents": len(documents)})
    thread_id = f"{claim_id}:{uuid.uuid4().hex[:8]}"            # one checkpoint thread per run
    _run(claim_id, thread_id, lambda: run_claim_graph(claim_id, bundle["title"], documents, thread_id=thread_id))


def resolve_hold(claim_id: str, action: str, note: str, user: str) -> None:
    """A person decides a claim that is ON_HOLD in the document queue: continue or stop."""
    bundle = db.get_bundle(claim_id)
    result = bundle.get("result") or {}
    thread_id = result.get("thread_id")
    if not thread_id or not waiting_on_hold(thread_id):
        raise ValueError("the claim is not waiting at the page-reading hold")
    answer = Command(resume={"action": action, "note": note, "reviewer": user})
    if action == "continue":
        ensure_idle(claim_id)
    audit.record(claim_id, user, f"hold_{action}", detail={"note": note, "thread_id": thread_id})
    if action == "stop":
        _finish(claim_id, thread_id, get_claim_graph().invoke(answer, config=run_config(thread_id)))
        return
    db.update_bundle(claim_id, "PROCESSING", keep_result=True, updated_by=user)
    _executor.submit(_run, claim_id, thread_id, lambda: get_claim_graph().invoke(answer, config=run_config(thread_id)))


def _without_resume(result: dict) -> dict:
    return {k: v for k, v in result.items() if k != "resume"}


def stop_claim(claim_id: str, user: str, note: str = "") -> str:
    """Stop a claim at once. A running claim's model call in flight is abandoned and no further
    agent starts; its run is checkpointed, so resume_claim continues from the last finished step."""
    bundle = db.get_bundle(claim_id)
    if bundle["status"] == "STOPPED":
        return "STOPPED"
    result = bundle.get("result") or {}
    resume: dict = {"from": bundle["status"]}
    if bundle["status"] in RUNNING:
        _stop_requested.add(claim_id)
        if claim_id in _running:
            resume["thread_id"] = _running[claim_id]
    db.update_bundle(claim_id, "STOPPED", decision=bundle.get("decision"), confidence=bundle.get("confidence"),
                     needs_review=False, result={**result, "resume": resume}, updated_by=user)
    audit.record(claim_id, user, "claim_stopped", detail={"note": note, "from": bundle["status"]})
    return "STOPPED"


def _continue_run(claim_id: str, thread_id: str) -> None:
    bundle = db.get_bundle(claim_id, with_result=False)
    if claim_id in _stop_requested or bundle is None or bundle["status"] != "QUEUED":   # stopped again while queued
        _stop_requested.discard(claim_id)
        return
    db.update_bundle(claim_id, "PROCESSING", keep_result=True)
    _run(claim_id, thread_id, lambda: get_claim_graph().invoke(None, config=run_config(thread_id)))


def resume_claim(claim_id: str, user: str) -> str:
    """Undo a stop. A claim that was waiting for a person goes back to waiting; a run that was
    stopped part-way continues from its checkpoint; anything else runs again from the start
    (pages already read come from the model cache). Returns the new status."""
    bundle = db.get_bundle(claim_id)
    if bundle["status"] != "STOPPED":
        raise ValueError(f"only a stopped claim can be resumed; this one is {bundle['status'].lower()}")
    if claim_id in _stop_requested:
        raise ClaimBusy("the claim is still stopping; try again in a moment")
    result = bundle.get("result") or {}
    resume = result.get("resume") or {}
    before = resume.get("from")
    waiting = result.get("thread_id") and paused_at(result["thread_id"])
    if before in ("ON_HOLD", "NEEDS_REVIEW") and waiting:      # it was waiting for a person
        db.update_bundle(claim_id, before, decision=bundle.get("decision"), confidence=bundle.get("confidence"),
                         needs_review=True, result=_without_resume(result), updated_by=user)
        audit.record(claim_id, user, "claim_resumed", detail={"to": before})
        return before
    ensure_idle(claim_id)
    thread_id = resume.get("thread_id")
    if thread_id and paused_at(thread_id):                    # stopped part-way: continue from the checkpoint
        db.update_bundle(claim_id, "QUEUED", result=_without_resume(result), updated_by=user)
        audit.record(claim_id, user, "claim_resumed",
                     detail={"thread_id": thread_id, "resume_at": list(paused_at(thread_id))})
        _executor.submit(_continue_run, claim_id, thread_id)
        return "QUEUED"
    audit.record(claim_id, user, "claim_resumed", detail={"note": "nothing to continue from: running it again"})
    rerun_claim(claim_id, user)
    return "QUEUED"


def submit_claim(title: str, pages: list[tuple[str, str, str | None]], created_by: str,
                 patient_id: str | None = None) -> str:
    """pages: (stored path, display name, document type or None to classify). Returns the claim id."""
    ensure_idle()
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
    """Queue the claim again. doc_types {doc_id: type} corrects the classifier first.
    Pages already read are served from the model cache, so only new or failed pages cost time."""
    ensure_idle(claim_id)
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


def replace_document(claim_id: str, doc_id: str, path: str, name: str, user: str) -> None:
    """Re-upload one page (e.g. an UNKNOWN or unreadable one), then re-run the claim."""
    ensure_idle(claim_id)
    row = {r["case_id"]: r for r in db.bundle_documents(claim_id)}.get(doc_id)
    if row is None:
        raise KeyError(doc_id)
    db.replace_case_file(doc_id, name, {"file_path": path})
    audit.record(claim_id, user, "document_replaced", doc_id=doc_id,
                 detail={"file_name": name, "from": row["file_name"], "previous_type": row.get("doc_type")})
    rerun_claim(claim_id, user)


def replace_all_documents(claim_id: str, pages: list[tuple[str, str]], user: str) -> None:
    """Re-upload every page of the claim, then re-run it."""
    ensure_idle(claim_id)
    bundle = db.get_bundle(claim_id, with_result=False)
    old = [r["file_name"] for r in db.bundle_documents(claim_id)]
    db.delete_bundle_documents(claim_id)
    for path, name in pages:
        db.save_case({"bundle_id": claim_id, "case_id": str(uuid.uuid4()), "file_name": name, "doc_type": None,
                      "status": "QUEUED", "decision": None, "confidence": None, "needs_review": False,
                      "result": {"file_path": path}, "override": None,
                      "patient_id": bundle.get("patient_id"), "created_by": user})
    audit.record(claim_id, user, "documents_replaced",
                 detail={"from": old, "files": [n for _, n in pages], "documents": len(pages)})
    rerun_claim(claim_id, user)


def _remove_upload(path: str | None) -> None:
    """Delete an uploaded page file; only files inside the upload folder are touched."""
    if not path:
        return
    file = pathlib.Path(path).resolve()
    if file.is_relative_to(config.UPLOAD_DIR.resolve()):
        file.unlink(missing_ok=True)


def delete_claim(claim_id: str, user: str) -> None:
    """Delete a claim that is not running: its pages, uploaded files, stored history and
    checkpoints. The audit log keeps its history, plus who deleted it."""
    bundle = db.get_bundle(claim_id, with_result=False)
    if bundle is None:
        raise KeyError(claim_id)
    if bundle.get("status") in RUNNING or claim_id in _running:
        raise ValueError("the claim is still being processed: stop it first")
    docs = db.bundle_documents(claim_id)
    saver = getattr(get_claim_graph(), "checkpointer", None)
    threads = set(db.checkpoint_threads(claim_id)) | {t for t in getattr(saver, "storage", {}) if t.startswith(f"{claim_id}:")}
    db.delete_bundle(claim_id)
    for thread_id in threads:                       # forget the in-memory copy too
        if hasattr(saver, "delete_thread"):
            saver.delete_thread(thread_id)
    for d in docs:
        _remove_upload((d.get("result") or {}).get("file_path"))
    audit.record(claim_id, user, "claim_deleted",
                 detail={"title": bundle["title"], "patient_id": bundle.get("patient_id"),
                         "status": bundle.get("status"), "decision": bundle.get("decision"),
                         "files": [d["file_name"] for d in docs]})


def delete_document(claim_id: str, doc_id: str, user: str) -> None:
    """Delete one page of a claim, then re-run the claim on the pages left. The last page
    cannot be deleted: delete the claim instead."""
    ensure_idle(claim_id)
    docs = db.bundle_documents(claim_id)
    row = next((r for r in docs if r["case_id"] == doc_id), None)
    if row is None:
        raise KeyError(doc_id)
    if len(docs) == 1:
        raise ValueError("this is the claim's only page: delete the claim instead")
    db.delete_case(doc_id)
    _remove_upload((row.get("result") or {}).get("file_path"))
    audit.record(claim_id, user, "document_deleted", doc_id=doc_id,
                 detail={"file_name": row["file_name"], "doc_type": row.get("doc_type")})
    rerun_claim(claim_id, user)


STATUS_BY_REVIEW = {"APPROVE": "APPROVED", "REJECT": "REJECTED", "ESCALATE": "NEEDS_REVIEW"}


def override_decision(claim_id: str, decision: str, note: str, user: str) -> None:
    """Ops staff decide a claim; its status follows the decision (APPROVED, REJECTED, or still
    NEEDS_REVIEW for "keep escalated"). An escalated claim whose run is paused at human_review
    is resumed from its checkpoint with an approve or reject; "keep escalated" leaves it paused
    and in the queue. Any other claim gets the decision recorded as an override. Either way the
    agents' decision is kept."""
    bundle = db.get_bundle(claim_id)
    original = bundle.get("decision")
    result = bundle.get("result") or {}
    thread_id = result.get("thread_id")
    status = STATUS_BY_REVIEW[decision]
    if decision == "ESCALATE":                     # keep escalated: a person still has to decide
        override = {"decision": decision, "note": note, "reviewer": user,
                    "reviewed_at": audit.now(), "original_decision": original}
        db.update_bundle(claim_id, status, decision="ESCALATE", confidence=bundle.get("confidence"),
                         needs_review=True, override=override, keep_result=True, updated_by=user)
        audit.record(claim_id, user, "review_kept_escalated", detail={"from": original, "note": note})
        return
    if thread_id and waiting_for_human(thread_id):
        state = get_claim_graph().invoke(Command(resume={"decision": decision, "note": note, "reviewer": user}),
                                         config=run_config(thread_id))
        review = state.get("review") or {}
        audit.record(claim_id, user, "human_review_resumed",
                     detail={"from": original, "decision": decision, "note": note, "thread_id": thread_id})
        db.update_bundle(claim_id, status, decision=decision, confidence=bundle.get("confidence"),
                         needs_review=False, override=review,
                         result={**result, "review": review, "awaiting_human": False, "trace": state.get("trace")},
                         updated_by=user)
        return
    override = {"decision": decision, "note": note, "reviewer": user,
                "reviewed_at": audit.now(), "original_decision": original}
    db.update_bundle(claim_id, status, decision=decision, confidence=bundle.get("confidence"),
                     needs_review=False, override=override, keep_result=True, updated_by=user)
    audit.record(claim_id, user, "human_override",
                 detail={"from": original, "decision": decision, "note": note})


def recover_interrupted_claims() -> int:
    """Claims still QUEUED or PROCESSING at startup were lost with the previous process: queue them again."""
    stuck = db.list_bundles(status="PROCESSING") + db.list_bundles(status="QUEUED")
    for b in stuck:
        db.update_bundle(b["bundle_id"], "QUEUED", keep_result=True)    # process_claim only starts QUEUED claims
        audit.record(b["bundle_id"], "system", "claim_requeued_after_restart")
        _executor.submit(process_claim, b["bundle_id"])
    return len(stuck)
