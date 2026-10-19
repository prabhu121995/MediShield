"""
FastAPI service for the MediShield intake pipeline.

    uvicorn app.main:app --reload --port 8000     (run from backend/)

Access:
  public : /api/health, /api/auth/login
  agent  : cases, patients (list, view, create, edit, upload, review, document image)
  admin  : policy ingestion, deleting claims, pages and patients, and everything agents can do
"""

from __future__ import annotations

import pathlib
import re
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from typing import Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from app import audit, audit_analytics, auth, case_timeline, config, db, log_analytics, logdb, uploads, policy_chat as policy_assistant
from app.bundles import summarize_bundle
from app.claim import intake, service as claims_service
from app.claim.pages import in_processing_order
from app.policy_store import get_policy_store, ingest_pdf
from app.service import document_path, enqueue_upload, recover_interrupted, retry_case, review_case

IMAGE_EXTS = config.SINGLE_UPLOAD_EXTENSIONS          # all limits from .env
MAX_BATCH_FILES = config.MAX_BATCH_FILES
MAX_BUNDLE_DOCUMENTS = config.MAX_CLAIM_PAGES        # after PDFs are split into pages
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
DOC_TYPES = Literal["CLAIM_FORM", "ID_DOCUMENT", "DISCHARGE_SUMMARY", "PRESCRIPTION",
                    "POLICY_AMENDMENT"]
DECISIONS = Literal["APPROVE", "REJECT", "ESCALATE"]
ANY_USER = Depends(auth.current_user)
ADMIN_ONLY = Depends(auth.require_role("admin"))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    logdb.install("api")                       # logs go to MySQL (app_log), not to files
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    recover_interrupted()
    claims_service.recover_interrupted_claims()
    yield


app = FastAPI(title="MediShield Intake API", version="0.3.0", lifespan=lifespan)
@app.exception_handler(claims_service.ClaimBusy)
def _claim_busy(_request, exc: claims_service.ClaimBusy):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["*"], allow_headers=["*"],
)


class LoginIn(BaseModel):
    username: str
    password: str


class ReviewIn(BaseModel):
    doc_type: DOC_TYPES | None = None
    decision: DECISIONS | None = None
    note: str | None = None


class PatientIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    date_of_birth: date | None = None
    gender: str | None = Field(default=None, max_length=16)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=255)
    address: str | None = Field(default=None, max_length=255)
    policy_number: str | None = Field(default=None, max_length=32)
    member_id: str | None = Field(default=None, max_length=32)


def _clean_patient(body: PatientIn, patient_id: str | None = None) -> dict:
    """Trim text, turn blanks into NULL, and check the rules. The insurance member ID is
    required, must belong to a known member, and may belong to only one patient."""
    data = {k: (v.strip() or None) if isinstance(v, str) else v for k, v in body.model_dump().items()}
    data["full_name"] = body.full_name.strip()
    if data["email"] and not EMAIL_RE.fullmatch(data["email"]):
        raise HTTPException(422, "email address is not valid")
    if data["date_of_birth"] and data["date_of_birth"] > date.today():
        raise HTTPException(422, "date of birth cannot be in the future")
    if not data["member_id"]:
        raise HTTPException(422, "insurance member ID is required")
    member = db.get_repository().find_member(data["member_id"])
    if member is None:
        raise HTTPException(422, f"no insurance member with id '{data['member_id']}'")
    owner = db.find_patient_by_member(data["member_id"], exclude=patient_id)
    if owner is not None:
        raise HTTPException(409, f"member '{data['member_id']}' is already registered to {owner['full_name']}")
    data["policy_number"] = data["policy_number"] or member["policy_number"]
    return data


def _save_upload(upload: UploadFile, allowed: set[str], folder: pathlib.Path) -> tuple[str, str]:
    ext = pathlib.Path(upload.filename or "").suffix.lower()
    if ext not in allowed:
        raise HTTPException(415, f"unsupported file type '{ext}'; allowed: {sorted(allowed)}")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{uuid.uuid4()}{ext}"
    target.write_bytes(upload.file.read())
    return str(target), upload.filename or target.name


@app.get("/api/health")
def health():
    return {"status": "ok", "policy_chunks": get_policy_store().count()}


@app.post("/api/auth/login")
def login(body: LoginIn):
    user = auth.authenticate(body.username.strip(), body.password)
    if user is None:
        audit.record_entity("user", body.username.strip()[:64] or "-", body.username.strip()[:64] or "-",
                            "login_failed", status="error")
        raise HTTPException(401, "invalid username or password")
    audit.record_entity("user", user["username"], user["username"], "login", detail={"role": user["role"]})
    return {"token": auth.create_token(user), "user": user}


@app.get("/api/auth/me")
def me(user: dict = ANY_USER):
    return user


@app.get("/api/dashboard/stats")
def dashboard_stats(user: dict = ANY_USER):
    # queue_counts: the sidebar badges, counted with each queue page's own filter so they match.
    return {**db.dashboard_stats(), "queue_counts": {name: db.count_bundles(**f) for name, f in QUEUES.items()}}


@app.get("/api/cases")
def list_cases(decision: DECISIONS | None = None, patient_id: str | None = None,
               user: dict = ANY_USER):
    return db.list_cases(decision=decision, patient_id=patient_id)


@app.post("/api/cases", status_code=202)
def create_case(file: UploadFile = File(...), doc_type: DOC_TYPES | None = Form(None),
                user: dict = ANY_USER):
    """Accepts one document and returns at once. Poll GET /api/cases/{id} for the outcome."""
    path, name = _save_upload(file, IMAGE_EXTS, config.UPLOAD_DIR)
    case_id = enqueue_upload(path, name, doc_type=doc_type or None, created_by=user["username"])
    audit.record_entity("case", case_id, user["username"], "document_uploaded", detail={"file_name": name})
    return db.get_case(case_id)


@app.post("/api/cases/{case_id}/retry", status_code=202)
def retry(case_id: str, user: dict = ANY_USER):
    case = retry_case(case_id, user=user["username"])
    if case is None:
        raise HTTPException(404, "case not found")
    audit.record_entity("case", case_id, user["username"], "document_retried")
    return case


@app.get("/api/cases/{case_id}")
def get_case(case_id: str, user: dict = ANY_USER):
    case = db.get_case(case_id)
    if case is None:
        raise HTTPException(404, "case not found")
    return case


@app.get("/api/cases/{case_id}/document")
def get_document(case_id: str, user: dict = ANY_USER):
    path = document_path(case_id)
    if path is None or not path.is_file():
        raise HTTPException(404, "document not found")
    return FileResponse(path)


@app.post("/api/cases/{case_id}/review")
def review(case_id: str, body: ReviewIn, user: dict = ANY_USER):
    if body.doc_type is None and body.decision is None:
        raise HTTPException(422, "send doc_type to re-run, or decision to override")
    try:
        record = review_case(case_id, doc_type=body.doc_type, decision=body.decision,
                             note=body.note, user=user["username"])
    except KeyError:
        raise HTTPException(404, "case not found")
    record["override"] = {**(record.get("override") or {}), "reviewer": user["username"]}
    db.save_case(record)
    audit.record_entity("case", case_id, user["username"], "document_reviewed",
                        detail={"doc_type": body.doc_type, "decision": body.decision, "note": body.note})
    return db.get_case(case_id)


# ---- patients -------------------------------------------------------------

@app.get("/api/patients")
def list_patients(q: str | None = None, limit: int | None = None, offset: int = Query(0, ge=0),
                  user: dict = ANY_USER):
    return db.list_patients(search=(q or "").strip() or None, limit=_limit(limit), offset=offset)


@app.post("/api/patients", status_code=201)
def create_patient(body: PatientIn, user: dict = ANY_USER):
    data = _clean_patient(body)
    patient_id = str(uuid.uuid4())
    db.insert_patient(patient_id, data, created_by=user["username"])
    audit.record_entity("patient", patient_id, user["username"], "patient_created",
                        detail={"full_name": data["full_name"], "member_id": data.get("member_id")})
    return db.get_patient(patient_id)


@app.get("/api/patients/{patient_id}")
def get_patient(patient_id: str, user: dict = ANY_USER):
    patient = db.get_patient(patient_id)
    if patient is None:
        raise HTTPException(404, "patient not found")
    return patient


@app.put("/api/patients/{patient_id}")
def update_patient(patient_id: str, body: PatientIn, user: dict = ANY_USER):
    if db.get_patient(patient_id) is None:
        raise HTTPException(404, "patient not found")
    before = db.get_patient(patient_id) or {}
    data = _clean_patient(body, patient_id=patient_id)
    db.update_patient(patient_id, data, updated_by=user["username"])
    changed = sorted(k for k, v in data.items() if str(before.get(k) or "") != str(v or ""))
    audit.record_entity("patient", patient_id, user["username"], "patient_updated", detail={"changed": changed})
    return db.get_patient(patient_id)


@app.delete("/api/patients/{patient_id}", status_code=204)
def delete_patient(patient_id: str, user: dict = ADMIN_ONLY):
    """Delete a patient with no claims or pages linked (delete those first). Audited."""
    patient = db.get_patient(patient_id)
    if patient is None:
        raise HTTPException(404, "patient not found")
    usage = db.patient_usage(patient_id)
    if usage["claims"] or usage["pages"]:
        raise HTTPException(409, f"{patient['full_name']} has {usage['claims']} claim(s) and {usage['pages']} page(s) "
                                 "linked: delete those first")
    db.delete_patient(patient_id)
    audit.record_entity("patient", patient_id, user["username"], "patient_deleted",
                        detail={"full_name": patient["full_name"], "member_id": patient.get("member_id")})
    return Response(status_code=204)


@app.get("/api/patients/{patient_id}/cases")
def patient_cases(patient_id: str, user: dict = ANY_USER):
    if db.get_patient(patient_id) is None:
        raise HTTPException(404, "patient not found")
    return db.list_cases(patient_id=patient_id)


@app.get("/api/patients/{patient_id}/claim-bundles")
def patient_bundles(patient_id: str, user: dict = ANY_USER):
    """The patient's claims, newest first, with their pages."""
    if db.get_patient(patient_id) is None:
        raise HTTPException(404, "patient not found")
    return _views(db.list_bundles(patient_id=patient_id, limit=config.LIST_LIMIT_MAX))


@app.post("/api/patients/{patient_id}/cases", status_code=202)
def upload_patient_cases(patient_id: str, files: list[UploadFile] = File(...),
                         doc_type: DOC_TYPES | None = Form(None), user: dict = ANY_USER):
    """Multi-part upload: many documents for one patient in a single request.
    Every file is checked before any is saved, so a bad file rejects the whole batch."""
    if db.get_patient(patient_id) is None:
        raise HTTPException(404, "patient not found")
    if not files or len(files) > MAX_BATCH_FILES:
        raise HTTPException(422, f"send between 1 and {MAX_BATCH_FILES} files")
    for f in files:
        ext = pathlib.Path(f.filename or "").suffix.lower()
        if ext not in IMAGE_EXTS:
            raise HTTPException(415, f"unsupported file type '{ext}' in '{f.filename}'")
    case_ids = []
    for f in files:
        path, name = _save_upload(f, IMAGE_EXTS, config.UPLOAD_DIR)
        case_ids.append(enqueue_upload(path, name, doc_type=doc_type or None,
                                       patient_id=patient_id, created_by=user["username"]))
        audit.record_entity("case", case_ids[-1], user["username"], "document_uploaded",
                            detail={"file_name": name, "patient_id": patient_id})
    return [db.get_case(cid) for cid in case_ids]


# ---- case bundles: one case, many documents, one upload ----------------------

CLAIM_STATUSES = {"QUEUED", "PROCESSING", "APPROVED", "REJECTED", "NEEDS_REVIEW", "REVIEWED", "FAILED",
                  "ON_HOLD", "STOPPED"}


def _bundle_view(bundle: dict, docs: list[dict] | None = None) -> dict:
    """A claim with its pages. Claims decided by the claim graph carry their own status;
    older bundles (one decision per page) are rolled up as before."""
    if docs is None:
        docs = db.cases_for_bundles([bundle["bundle_id"]])[bundle["bundle_id"]]
    docs = in_processing_order(docs)                   # ID, claim form, discharge, prescription, amendment
    summary = summarize_bundle(docs)
    if bundle.get("status") in CLAIM_STATUSES:
        pending = bundle["status"] in ("QUEUED", "PROCESSING")
        summary = {**summary, "status": bundle["status"], "decision": bundle.get("decision"),
                   "processing": sum(1 for d in docs if d["status"] in ("QUEUED", "PROCESSING")) if pending else 0,
                   "needs_review": int(bool(bundle.get("needs_review")))}
    return {**bundle, "documents": docs, "summary": summary}


class ClaimReviewIn(BaseModel):
    decision: DECISIONS | None = None
    note: str | None = Field(default=None, max_length=1000)
    doc_types: dict[str, DOC_TYPES] | None = None      # {doc_id: corrected type}; re-runs the claim


def _views(bundles: list[dict]) -> list[dict]:
    """Claims with their pages: two queries in total, whatever the number of claims."""
    pages = db.cases_for_bundles([b["bundle_id"] for b in bundles])
    return [_bundle_view(b, pages[b["bundle_id"]]) for b in bundles]


# A queue is a filter over claims, newest first. Review / escalated: waiting for a person.
# Approved / rejected: the final decision, by the agents or a reviewer's override.
QUEUES = {
    "review": {"status": ("NEEDS_REVIEW", "ON_HOLD")},
    "escalated": {"status": "NEEDS_REVIEW", "decision": "ESCALATE"},
    "approved": {"decision": "APPROVE"},
    "rejected": {"decision": "REJECT"},
}


def _limit(limit: int | None) -> int:
    return max(1, min(limit or config.LIST_LIMIT_DEFAULT, config.LIST_LIMIT_MAX))


@app.get("/api/claim-bundles")
def list_bundles(status: str | None = None, limit: int | None = None, q: str | None = None,
                 offset: int = Query(0, ge=0), user: dict = ANY_USER):
    return _views(db.list_bundles(status=status, limit=_limit(limit), q=(q or "").strip() or None, offset=offset))


@app.get("/api/queues/{name}")
def claim_queue(name: Literal["review", "escalated", "approved", "rejected"], limit: int | None = None,
                q: str | None = None, offset: int = Query(0, ge=0), user: dict = ANY_USER):
    return _views(db.list_bundles(limit=_limit(limit), q=(q or "").strip() or None, offset=offset, **QUEUES[name]))


@app.get("/api/review-queue")
def review_queue(user: dict = ANY_USER):
    """Escalated claims waiting for ops staff, newest first."""
    return claim_queue("review", user=user)


@app.post("/api/claim-bundles/{bundle_id}/review")
def review_claim_bundle(bundle_id: str, body: ClaimReviewIn, user: dict = ANY_USER):
    """Human review. `doc_types` corrects the classifier and re-runs the claim; `decision`
    records the reviewer's final decision (a note is required for an override)."""
    bundle = db.get_bundle(bundle_id)
    if bundle is None:
        raise HTTPException(404, "case not found")
    if not body.doc_types and not body.decision:
        raise HTTPException(422, "send decision to decide the claim, or doc_types to correct pages and re-run")
    if bundle.get("status") in ("QUEUED", "PROCESSING"):
        raise HTTPException(409, "the claim is still being processed")
    if body.doc_types:
        try:
            claims_service.rerun_claim(bundle_id, user["username"], doc_types=dict(body.doc_types))
        except KeyError as exc:
            raise HTTPException(422, f"document {exc.args[0]} is not part of this claim") from exc
    else:
        if not (body.note or "").strip():
            raise HTTPException(422, "a note is required when overriding the decision")
        claims_service.override_decision(bundle_id, body.decision, body.note.strip(), user["username"])
    return get_bundle(bundle_id, user)


@app.post("/api/claim-bundles/{bundle_id}/rerun", status_code=202)
def rerun_claim_bundle(bundle_id: str, user: dict = ANY_USER):
    bundle = db.get_bundle(bundle_id, with_result=False)
    if bundle is None:
        raise HTTPException(404, "case not found")
    if bundle.get("status") in claims_service.RUNNING:
        raise HTTPException(409, "the claim is already queued")
    claims_service.rerun_claim(bundle_id, user["username"])
    return _bundle_view(db.get_bundle(bundle_id, with_result=False))


AUDIT_ENTITIES = Literal["claim", "case", "patient", "policy", "user"]
AUDIT_RANGES = Literal["24h", "7d", "30d", "all"]
# "agent_completed" rows carry the agents' investigation notes.
AUDIT_EVENTS = Literal["agent_completed"]


@app.get("/api/audit")
def audit_log(entity_type: AUDIT_ENTITIES | None = None, entity_id: str | None = None, actor: str | None = None,
              range: AUDIT_RANGES = "all", limit: int | None = None, q: str | None = None,
              offset: int = Query(0, ge=0), event: AUDIT_EVENTS | None = None, user: dict = ADMIN_ONLY):
    """The audit log across every entity (MySQL table audit_event), newest first. `entity_id`
    narrows it to one case: its claim events and its documents' events."""
    return db.search_audit_events(entity_type, entity_id, actor, _limit(limit), since=log_analytics.since_of(range),
                                  q=(q or "").strip() or None, offset=offset, event=event)


@app.get("/api/audit/claims")
def audit_claims(user: dict = ADMIN_ONLY):
    """The claims to pick from on the Audit log page (including claims deleted since)."""
    return audit_analytics.audit_claims(config.LIST_LIMIT_MAX)


@app.get("/api/audit/case/{case_id}")
def audit_case(case_id: str, user: dict = ADMIN_ONLY):
    """One case's audit trail as numbered steps, oldest first, grouped into runs, with the
    per-agent times: what the Audit log's Steps and Analytics tabs show."""
    t = case_timeline.case_timeline(case_id)
    if t is None:
        raise HTTPException(404, "case not found")
    return case_timeline.to_json(t)


@app.get("/api/audit/stats")
def audit_stats(range: AUDIT_RANGES = "24h", entity_type: AUDIT_ENTITIES | None = None, actor: str | None = None,
                event: AUDIT_EVENTS | None = None, entity_id: str | None = None, user: dict = ADMIN_ONLY):
    """Aggregates for the audit analytics tab."""
    return audit_analytics.stats(range, entity_type, actor, event, entity_id=entity_id)


@app.get("/api/audit/export.csv")
def audit_csv(range: AUDIT_RANGES = "24h", entity_type: AUDIT_ENTITIES | None = None, actor: str | None = None,
              event: AUDIT_EVENTS | None = None, entity_id: str | None = None, user: dict = ADMIN_ONLY):
    """The raw audit events in the current filter, as CSV (at most LOG_EXPORT_MAX_ROWS). With
    `entity_id`, that case's whole trail as numbered steps, oldest first."""
    if entity_id:
        return claim_audit_csv(entity_id, user)
    body = audit_analytics.export_csv(range, entity_type, actor, event, entity_id)
    audit.record_entity("user", user["username"], user["username"], "audit_exported",
                        detail={"format": "csv", "range": range, "entity_type": entity_type, "actor": actor,
                                "event": event, "case": entity_id})
    name = f"medishield-audit-raw-{range}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.csv"
    return Response(body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/audit/report.pdf")
def audit_pdf(range: AUDIT_RANGES = "24h", entity_type: AUDIT_ENTITIES | None = None, actor: str | None = None,
              event: AUDIT_EVENTS | None = None, entity_id: str | None = None, user: dict = ADMIN_ONLY):
    """The audit analysis as a PDF report. With `entity_id`, that case's report: its runs and
    each run's steps in order."""
    if entity_id:
        return claim_audit_pdf(entity_id, user)
    body = audit_analytics.report_pdf(range, entity_type, actor, event, entity_id)
    audit.record_entity("user", user["username"], user["username"], "audit_exported",
                        detail={"format": "pdf", "range": range, "entity_type": entity_type, "actor": actor,
                                "event": event, "case": entity_id})
    name = f"medishield-audit-analysis-{range}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.pdf"
    return Response(body, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


LOG_LEVELS = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
LOG_RANGES = Literal["24h", "7d", "30d", "all"]


@app.get("/api/logs")
def app_logs(level: LOG_LEVELS | None = None, source: str | None = None, q: str | None = None,
             range: LOG_RANGES = "all", limit: int | None = None, offset: int = Query(0, ge=0),
             user: dict = ADMIN_ONLY):
    """Application logs (MySQL table app_log), newest first."""
    return {"sources": db.log_sources(),
            "rows": db.search_logs(level, source, (q or "").strip() or None, _limit(limit),
                                   since=log_analytics.since_of(range), offset=offset)}


@app.get("/api/logs/stats")
def app_log_stats(range: LOG_RANGES = "24h", source: str | None = None, level: LOG_LEVELS | None = None,
                  user: dict = ADMIN_ONLY):
    """Aggregates for the analytics tab: volume by level over time, sources, HTTP status, top endpoints, errors."""
    return log_analytics.stats(range, source, level)


def _download_name(kind: str, range_: str, ext: str) -> str:
    return f"medishield-logs-{kind}-{range_}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.{ext}"


@app.get("/api/logs/export.csv")
def app_logs_csv(range: LOG_RANGES = "24h", source: str | None = None, level: LOG_LEVELS | None = None,
                 q: str | None = None, user: dict = ADMIN_ONLY):
    """The raw log rows in the current filter, as CSV (at most LOG_EXPORT_MAX_ROWS)."""
    body = log_analytics.export_csv(range, source, level, (q or "").strip() or None)
    audit.record_entity("user", user["username"], user["username"], "logs_exported",
                        detail={"format": "csv", "range": range, "source": source, "level": level})
    return Response(body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{_download_name("raw", range, "csv")}"'})


@app.get("/api/logs/report.pdf")
def app_logs_pdf(range: LOG_RANGES = "24h", source: str | None = None, level: LOG_LEVELS | None = None,
                 user: dict = ADMIN_ONLY):
    """The analysis (tiles, volume chart, tables) as a PDF report."""
    body = log_analytics.report_pdf(range, source, level)
    audit.record_entity("user", user["username"], user["username"], "logs_exported",
                        detail={"format": "pdf", "range": range, "source": source, "level": level})
    return Response(body, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{_download_name("analysis", range, "pdf")}"'})


@app.get("/api/claim-bundles/{bundle_id}/audit")
def claim_audit(bundle_id: str, user: dict = ANY_USER):
    if db.get_bundle(bundle_id, with_result=False) is None:
        raise HTTPException(404, "case not found")
    return db.list_audit_events(bundle_id)


@app.get("/api/claim-bundles/{bundle_id}/audit/export.csv")
def claim_audit_csv(bundle_id: str, user: dict = ANY_USER):
    """One case's whole audit trail as CSV, one row per step, oldest first (also for a case deleted since)."""
    t = case_timeline.case_timeline(bundle_id)
    if t is None:
        raise HTTPException(404, "case not found")
    audit.record(bundle_id, actor=user["username"], event="audit_exported", detail={"format": "csv"})
    return Response(case_timeline.steps_csv(t), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{_case_file(t, "steps", "csv")}"'})


@app.get("/api/claim-bundles/{bundle_id}/audit/report.pdf")
def claim_audit_pdf(bundle_id: str, user: dict = ANY_USER):
    """One case's audit as a PDF: its runs, and each run's steps in order (which agent was called,
    on what, when, how long it took, what it returned), with a timeline chart per run."""
    t = case_timeline.case_timeline(bundle_id)
    if t is None:
        raise HTTPException(404, "case not found")
    audit.record(bundle_id, actor=user["username"], event="audit_exported", detail={"format": "pdf"})
    return Response(case_timeline.report_pdf(t), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{_case_file(t, "audit", "pdf")}"'})


def _case_file(t: dict, kind: str, ext: str) -> str:
    title = re.sub(r"[^A-Za-z0-9_-]+", "-", t["case"]["title"])
    return f"medishield-case-{title}-{kind}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.{ext}"


def _store_claim_files(files: list[UploadFile], per_file_types: list[str | None]) -> list[tuple[str, str, str | None]]:
    """Check every file, then store them (a PDF becomes one image per page). Nothing is kept
    when any file is refused. Returns (stored path, display name, document type)."""
    checked = []
    for f, file_type in zip(files, per_file_types):
        data = f.file.read(uploads.MAX_FILE_BYTES + 1)
        try:
            uploads.check_upload(f.filename, data)
        except uploads.UploadError as exc:
            raise HTTPException(exc.status, exc.message) from exc
        checked.append((f.filename or "document", data, file_type))

    stored: list[tuple[str, str, str | None]] = []
    try:
        for name, data, file_type in checked:
            if pathlib.Path(name).suffix.lower() == ".pdf":
                pages = uploads.render_pdf_pages(data, config.UPLOAD_DIR)
                stored.extend((str(page), f"{pathlib.Path(name).stem} (page {n} of {len(pages)}).pdf", file_type)
                              for n, page in enumerate(pages, start=1))
            else:
                config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
                target = config.UPLOAD_DIR / f"{uuid.uuid4()}{pathlib.Path(name).suffix.lower()}"
                target.write_bytes(data)
                stored.append((str(target), name, file_type))
        if len(stored) > MAX_BUNDLE_DOCUMENTS:
            raise HTTPException(422, f"a case can hold at most {MAX_BUNDLE_DOCUMENTS} documents "
                                     f"(this upload has {len(stored)} after splitting PDFs)")
    except (uploads.UploadError, HTTPException) as exc:
        for path, _, _ in stored:                     # nothing from a refused upload stays on disk
            pathlib.Path(path).unlink(missing_ok=True)
        if isinstance(exc, uploads.UploadError):
            raise HTTPException(exc.status, exc.message) from exc
        raise
    return stored


def _claim_for_action(bundle_id: str, allow_running: bool = False) -> dict:
    bundle = db.get_bundle(bundle_id, with_result=False)
    if bundle is None:
        raise HTTPException(404, "case not found")
    if not allow_running and bundle.get("status") in claims_service.RUNNING:
        raise HTTPException(409, "the claim is still being processed")
    return bundle


@app.get("/api/claim-bundles/active")
def active_claim(user: dict = ANY_USER):
    """The claim being processed now (only one runs at a time), or null."""
    busy = claims_service.active_claim()
    return {"active": {k: busy[k] for k in ("bundle_id", "title", "status")} if busy else None}


def _required_documents() -> list[str]:
    """Document types every new claim must be uploaded with (rules: upload_required_documents,
    else required_documents). Only the upload gate uses this; decide() still escalates on
    required_documents."""
    rules = config.load_rules()
    return list(rules.get("upload_required_documents", rules.get("required_documents", [])))


@app.get("/api/claim-bundles/requirements")
def claim_requirements(user: dict = ANY_USER):
    """What the upload form checks before sending: the document types every claim needs
    and the upload limits."""
    rules = config.load_rules()
    return {"required_documents": _required_documents(),
            "max_files": MAX_BATCH_FILES, "max_pages": MAX_BUNDLE_DOCUMENTS,
            "filename_hints": rules.get("filename_hints") or {},
            "patient_ref_pattern": rules.get("patient_ref_pattern") or r"PT_\d+"}


@app.get("/api/claim-bundles/patient-ref/{member_id}")
def claim_patient_ref(member_id: str, user: dict = ANY_USER):
    """What a patient reference in a file name means before upload: a registered patient, a
    member that will be registered as a new patient, or nobody known."""
    return intake.resolve_patient(member_id.strip().upper())


class HoldIn(BaseModel):
    action: Literal["continue", "stop"]
    note: str | None = Field(default=None, max_length=1000)


@app.post("/api/claim-bundles/{bundle_id}/hold")
def resolve_claim_hold(bundle_id: str, body: HoldIn, user: dict = ANY_USER):
    """Human in the loop for a claim ON_HOLD after page reading: continue with the pages as
    they are (the claim agents run in the background), or stop the claim."""
    _claim_for_action(bundle_id)
    try:
        claims_service.resolve_hold(bundle_id, body.action, (body.note or "").strip(), user["username"])
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return get_bundle(bundle_id, user)


class StopIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


@app.post("/api/claim-bundles/{bundle_id}/stop")
def stop_claim_bundle(bundle_id: str, body: StopIn | None = None, user: dict = ANY_USER):
    """Stop a claim at once. A running claim abandons its model call in flight; resume continues it."""
    _claim_for_action(bundle_id, allow_running=True)
    state = claims_service.stop_claim(bundle_id, user["username"], ((body.note if body else None) or "").strip())
    return {**get_bundle(bundle_id, user), "stop": state}


@app.post("/api/claim-bundles/{bundle_id}/resume")
def resume_claim_bundle(bundle_id: str, user: dict = ANY_USER):
    """Resume a stopped claim from where it stopped."""
    _claim_for_action(bundle_id)
    try:
        state = claims_service.resume_claim(bundle_id, user["username"])
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {**get_bundle(bundle_id, user), "resume": state}


@app.post("/api/claim-bundles/{bundle_id}/documents/{doc_id}/replace", status_code=202)
def replace_claim_document(bundle_id: str, doc_id: str, file: UploadFile = File(...), user: dict = ANY_USER):
    """Re-upload one page (an UNKNOWN or unreadable one, say) and re-run the claim."""
    _claim_for_action(bundle_id)
    claims_service.ensure_idle(bundle_id)
    stored = _store_claim_files([file], [None])
    if len(stored) != 1:
        for path, _, _ in stored:
            pathlib.Path(path).unlink(missing_ok=True)
        raise HTTPException(422, "replace a page with exactly one page (a single image or a one-page PDF)")
    path, name, _ = stored[0]
    try:
        claims_service.replace_document(bundle_id, doc_id, path, name, user["username"])
    except KeyError as exc:
        pathlib.Path(path).unlink(missing_ok=True)
        raise HTTPException(404, f"document {exc.args[0]} is not part of this claim") from exc
    return get_bundle(bundle_id, user)


@app.post("/api/claim-bundles/{bundle_id}/replace", status_code=202)
def replace_claim_documents(bundle_id: str, files: list[UploadFile] = File(...), user: dict = ANY_USER):
    """Re-upload every page of the claim and re-run it."""
    _claim_for_action(bundle_id)
    claims_service.ensure_idle(bundle_id)
    if not files or len(files) > MAX_BATCH_FILES:
        raise HTTPException(422, f"attach between 1 and {MAX_BATCH_FILES} files")
    stored = _store_claim_files(files, [None] * len(files))
    claims_service.replace_all_documents(bundle_id, [(p, n) for p, n, _ in stored], user["username"])
    return get_bundle(bundle_id, user)


@app.post("/api/claim-bundles", status_code=202)
def create_bundle(title: str = Form(""), files: list[UploadFile] = File(...),
                        categories: list[str] = Form(default=[]),
                        patient_id: str | None = Form(None), doc_type: DOC_TYPES | None = Form(None),
                        user: dict = ANY_USER):
    """Creates one claim from every attached document, in one multi-part request, and queues
    it for the claim graph (ingest -> kyc || claims -> policy -> fraud -> decide).

    Accepts JPEG, PNG, TIFF and PDF. `categories`, if sent, has one entry per file (the
    document type for that file, or empty to let the classifier decide). Each required
    document type (rules: required_documents) must be among them, or nothing is stored. A PDF
    is split into one document per page. Every file is checked before anything is stored."""
    title = title.strip()
    if title and not 2 <= len(title) <= 160:
        raise HTTPException(422, "title must be between 2 and 160 characters")
    patient_id = (patient_id or "").strip() or None
    if patient_id and db.get_patient(patient_id) is None:
        raise HTTPException(404, "patient not found")
    if not files or len(files) > MAX_BATCH_FILES:
        raise HTTPException(422, f"attach between 1 and {MAX_BATCH_FILES} files")
    if categories and len(categories) != len(files):
        raise HTTPException(422, "categories must have one entry per file")

    valid_types = set(DOC_TYPES.__args__)
    per_file_types = []
    for i in range(len(files)):
        value = (categories[i] if categories else "").strip().upper()
        if value and value not in valid_types:
            raise HTTPException(422, f"unknown document category '{value}'")
        per_file_types.append(value or doc_type)

    # Every required document must be attached under its own type: a claim without them would
    # only be escalated, after paying for every page to be read.
    missing = [t for t in _required_documents() if t not in per_file_types]
    if missing:
        raise HTTPException(422, "attach the required document(s) before uploading: " + ", ".join(missing))

    # A file whose name points to another document type ("claim_PT_1.png" as the ID document)
    # is refused; a name with no hint ("aaaaaa.png") may go anywhere.
    rules = config.load_rules()
    for f, file_type in zip(files, per_file_types):
        named = intake.name_types(f.filename, rules)
        if file_type and named and file_type not in named:
            raise HTTPException(422, f"'{f.filename}' looks like {' / '.join(sorted(named))}, "
                                     f"not {file_type}: attach it to the right document")

    # The patient reference in the file names (an insurance member ID) must be one patient,
    # and the chosen patient's if one was chosen.
    refs = intake.patient_refs([f.filename for f in files], rules)
    if len(refs) > 1:
        raise HTTPException(422, "the files belong to different patients: " + ", ".join(sorted(refs)))
    ref = next(iter(refs), None)
    if ref and patient_id:
        chosen = (db.get_patient(patient_id) or {}).get("member_id")
        if chosen and chosen.upper() != ref:
            raise HTTPException(422, f"the files are for {ref} but the chosen patient is {chosen}")

    claims_service.ensure_idle()                     # one claim at a time: refuse before storing anything
    stored = _store_claim_files(files, per_file_types)
    if ref and not patient_id:                       # the files name the patient: link it, registering it if new
        patient_id = intake.register_patient(ref, user["username"])
    if not title:                                    # untitled: the case number, plus the patient's member ID
        case_no = db.next_case_no()
        member_id = (db.get_patient(patient_id) or {}).get("member_id") if patient_id else None
        title = f"{case_no}_{member_id}" if member_id else case_no    # else added once the pages name the patient

    # 3. Create the claim and queue it. The claim graph reads the pages one by one.
    bundle_id = claims_service.submit_claim(title, [(p, n, t or None) for p, n, t in stored],
                                            created_by=user["username"], patient_id=patient_id)
    return _bundle_view(db.get_bundle(bundle_id, with_result=False))


@app.get("/api/claim-bundles/{bundle_id}")
def get_bundle(bundle_id: str, user: dict = ANY_USER):
    bundle = db.get_bundle(bundle_id)
    if bundle is None:
        raise HTTPException(404, "case not found")
    return _bundle_view(bundle)


@app.delete("/api/claim-bundles/{bundle_id}", status_code=204)
def delete_bundle(bundle_id: str, user: dict = ADMIN_ONLY):
    """Delete a claim that is not running, with its pages and files. The audit log keeps it."""
    try:
        claims_service.delete_claim(bundle_id, user["username"])
    except KeyError as exc:
        raise HTTPException(404, "case not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return Response(status_code=204)


@app.delete("/api/claim-bundles/{bundle_id}/documents/{doc_id}", status_code=202)
def delete_claim_document(bundle_id: str, doc_id: str, user: dict = ADMIN_ONLY):
    """Delete one page of a claim; the claim re-runs on the pages left."""
    _claim_for_action(bundle_id)
    try:
        claims_service.delete_document(bundle_id, doc_id, user["username"])
    except KeyError as exc:
        raise HTTPException(404, f"document {exc.args[0]} is not part of this claim") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return get_bundle(bundle_id, user)


@app.get("/api/policies")
def list_policies(user: dict = ANY_USER):
    return db.list_policy_documents()


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=2000)


class PolicyChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=16)


@app.post("/api/policies/chat")
def policy_chat(body: PolicyChatIn, user: dict = ADMIN_ONLY):
    """Ask the policy assistant. Answers come from the indexed policy clauses only."""
    history = [t.model_dump() for t in body.history]
    try:
        return policy_assistant.ask(body.message.strip(), history, get_policy_store())
    except RuntimeError as exc:                 # LLM unavailable or rate limited
        raise HTTPException(503, f"policy assistant unavailable: {exc}") from exc


@app.post("/api/policies", status_code=201)
def upload_policy(file: UploadFile = File(...), user: dict = ADMIN_ONLY):
    path, name = _save_upload(file, {".pdf"}, config.POLICY_PDF_DIR)
    try:
        chunks = ingest_pdf(path, name, get_policy_store())
    except Exception as exc:
        raise HTTPException(500, f"ingestion failed: {type(exc).__name__}: {exc}") from exc
    db.add_policy_document(name, chunks)
    audit.record_entity("policy", name, user["username"], "policy_ingested", detail={"chunks": chunks})
    return {"filename": name, "chunks": chunks}
