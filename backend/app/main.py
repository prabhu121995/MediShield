"""
FastAPI service for the MediShield intake pipeline.

    uvicorn app.main:app --reload --port 8000     (run from backend/)

Access:
  public : /api/health, /api/auth/login
  agent  : cases, patients (list, view, create, edit, upload, review, document image)
  admin  : policy ingestion, and everything agents can do
"""

from __future__ import annotations

import pathlib
import re
import uuid
from contextlib import asynccontextmanager
from datetime import date
from typing import Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app import audit, auth, config, db, uploads, policy_chat as policy_assistant
from app.bundles import summarize_bundle
from app.claim import service as claims_service
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
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    recover_interrupted()
    claims_service.recover_interrupted_claims()
    yield


app = FastAPI(title="MediShield Intake API", version="0.3.0", lifespan=lifespan)
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
    return db.dashboard_stats()


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
def list_patients(q: str | None = None, user: dict = ANY_USER):
    return db.list_patients(search=(q or "").strip() or None)


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


@app.get("/api/patients/{patient_id}/cases")
def patient_cases(patient_id: str, user: dict = ANY_USER):
    if db.get_patient(patient_id) is None:
        raise HTTPException(404, "patient not found")
    return db.list_cases(patient_id=patient_id)


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

CLAIM_STATUSES = {"QUEUED", "PROCESSING", "APPROVED", "REJECTED", "NEEDS_REVIEW", "REVIEWED", "FAILED"}


def _bundle_view(bundle: dict, docs: list[dict] | None = None) -> dict:
    """A claim with its pages. Claims decided by the claim graph carry their own status;
    older bundles (one decision per page) are rolled up as before."""
    if docs is None:                                   # upload order, the order the pages are read in
        docs = db.cases_for_bundles([bundle["bundle_id"]])[bundle["bundle_id"]]
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


# A queue is a filter over claims. Review: escalated, waiting for a person (oldest first).
# Approved / rejected: the final decision, by the agents or a reviewer's override (newest first).
QUEUES = {
    "review": {"status": "NEEDS_REVIEW", "oldest_first": True},
    "approved": {"decision": "APPROVE"},
    "rejected": {"decision": "REJECT"},
}


def _limit(limit: int | None) -> int:
    return max(1, min(limit or config.LIST_LIMIT_DEFAULT, config.LIST_LIMIT_MAX))


@app.get("/api/claim-bundles")
def list_bundles(status: str | None = None, limit: int | None = None, user: dict = ANY_USER):
    return _views(db.list_bundles(status=status, limit=_limit(limit)))


@app.get("/api/queues/{name}")
def claim_queue(name: Literal["review", "approved", "rejected"], limit: int | None = None, user: dict = ANY_USER):
    return _views(db.list_bundles(limit=_limit(limit), **QUEUES[name]))


@app.get("/api/review-queue")
def review_queue(user: dict = ANY_USER):
    """Escalated claims waiting for ops staff, oldest first."""
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
    if bundle.get("status") in ("QUEUED", "PROCESSING"):
        raise HTTPException(409, "the claim is already queued")
    claims_service.rerun_claim(bundle_id, user["username"])
    return _bundle_view(db.get_bundle(bundle_id, with_result=False))


@app.get("/api/audit")
def audit_log(entity_type: Literal["claim", "case", "patient", "policy", "user"] | None = None,
              entity_id: str | None = None, actor: str | None = None, limit: int | None = None,
              user: dict = ADMIN_ONLY):
    """The audit log across every entity (MySQL table audit_event), newest first."""
    return db.search_audit_events(entity_type, entity_id, actor, _limit(limit))


@app.get("/api/claim-bundles/{bundle_id}/audit")
def claim_audit(bundle_id: str, user: dict = ANY_USER):
    if db.get_bundle(bundle_id, with_result=False) is None:
        raise HTTPException(404, "case not found")
    return db.list_audit_events(bundle_id)


@app.post("/api/claim-bundles", status_code=202)
def create_bundle(title: str = Form(""), files: list[UploadFile] = File(...),
                        categories: list[str] = Form(default=[]),
                        patient_id: str | None = Form(None), doc_type: DOC_TYPES | None = Form(None),
                        user: dict = ANY_USER):
    """Creates one claim from every attached document, in one multi-part request, and queues
    it for the claim graph (ingest -> kyc || claims -> policy -> fraud -> decide).

    Accepts JPEG, PNG, TIFF and PDF. `categories`, if sent, has one entry per file (the
    document type for that file, or empty to let the classifier decide). A PDF is split into
    one document per page. Every file is checked before anything is stored."""
    title = title.strip() or f"Claim {date.today().isoformat()} ({len(files)} document{'s' if len(files) != 1 else ''})"
    if not 2 <= len(title) <= 160:
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

    # 1. Read and check every file. Nothing is stored until all of them pass.
    checked = []
    for f, file_type in zip(files, per_file_types):
        data = f.file.read(uploads.MAX_FILE_BYTES + 1)
        try:
            uploads.check_upload(f.filename, data)
        except uploads.UploadError as exc:
            raise HTTPException(exc.status, exc.message) from exc
        checked.append((f.filename or "document", data, file_type))

    # 2. Store the files. A PDF becomes one stored image per page, all written before the case exists.
    stored: list[tuple[str, str, str | None]] = []       # (path, display name, document type)
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
